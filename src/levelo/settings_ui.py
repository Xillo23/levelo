"""Settings window, laid out like the Windows 11 Settings app: navigation on the
left, "setting cards" on the right (icon, title, description, control).

Every page is built once and then only UPDATED in place once a second (texts,
switches, status), so nothing flickers, jumps or loses the scroll position.
A part is rebuilt only when its structure really changes (a device plugged in,
a new app in the list, a different search)."""

import ctypes
import os
import re
import time
import tkinter as tk
from tkinter import filedialog, ttk

from . import APP_NAME, BETA, __version__, i18n, winsys
from .appinfo import app_title, audio_history, exe_icon
from .engine import CALL_APPS
from .i18n import t
from .icons import app_icon, png_b64
from .storage import DATA_DIR

# Segoe Fluent Icons / MDL2 glyphs
G = dict(general="", devices="", apps="", calls="",
         about="", power="", level="", start="",
         language="", speakers="", headphones="", headset="",
         display="", auto="", pick="", app="", search="",
         update="", web="", bug="", folder="", heart="",
         info="", warn="", system="", open="", add="")

# KSJACK form factor of an output device -> icon
FORM_ICON = {1: "speakers", 3: "headphones", 4: "headset", 5: "headset", 9: "display",
             8: "display"}

PAGES = ("general", "devices", "apps", "calls", "about")
PRESET_ORDER = ("light", "normal", "night")
SEEN_MAX = 60
HISTORY_TTL = 60.0      # seconds the Windows audio history is cached

# Display names of the call apps that are recognised (shown on the Calls page)
CALL_NAMES = ("Discord", "Microsoft Teams", "Zoom", "Skype", "Slack", "WhatsApp",
              "Telegram", "Signal", "Viber", "TeamSpeak", "Mumble", "Webex",
              "Xbox Party Chat", "RingCentral", "GoTo Meeting", "Element", "Guilded")

# Store apps whose exe has no readable description
FRIENDLY = {"whatsapp.root.exe": "WhatsApp", "whatsapp.exe": "WhatsApp",
            "ms-teams.exe": "Microsoft Teams", "msteams.exe": "Microsoft Teams",
            "spotify.exe": "Spotify", "netflix.exe": "Netflix", "music.ui.exe": "Media Player",
            "microsoft.media.player.exe": "Media Player",
            "phoneexperiencehost.exe": "Phone Link", "telegram.exe": "Telegram",
            "xboxpartychathost.exe": "Xbox Party Chat"}


def split_device_name(name):
    """'Speakers (2- Realtek Audio)' -> ('Speakers', 'Realtek Audio'), like the
    two lines of the Windows sound settings."""
    if name.endswith(")") and " (" in name:
        i = name.index(" (")
        return name[:i], re.sub(r"^\d+-\s*", "", name[i + 2:-1])
    return name, ""


def short(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"


def _page_of(widget):
    """The page frame a widget belongs to (it holds that page's cards)."""
    while widget is not None and not hasattr(widget, "cards"):
        widget = widget.master
    return widget


class Card:
    """A settings card: [icon] title / description ............ [control]."""

    def __init__(self, ui, parent, glyph, title, desc="", image=None):
        app, px, pal = ui.app, ui.app.px, ui.app.pal
        self.ui = ui
        c = self.frame = self.outer = tk.Frame(
            parent, bg=pal["card"], highlightthickness=1, highlightbackground=pal["border"],
            highlightcolor=pal["border"], bd=0)
        c.pack(fill="x", padx=px(12), pady=(0, px(4)))
        c.columnconfigure(0, minsize=px(26))
        c.columnconfigure(1, weight=1)
        if image is not None:
            self.icon = tk.Label(c, image=image, bg=pal["card"], bd=0)
        else:
            self.icon = tk.Label(c, text=glyph, font=(app.icon_font, 14), bg=pal["card"],
                                 fg=pal["fg"], bd=0)
        self.icon.grid(row=0, column=0, rowspan=2, padx=(px(16), px(14)), pady=px(12))
        # an initial wrap width: a long text can never push the window wider
        self.title = tk.Label(c, text=title, bg=pal["card"], fg=pal["fg"], font=app.F["body"],
                              bd=0, anchor="w", justify="left", wraplength=px(420))
        self.desc = tk.Label(c, text=desc, bg=pal["card"], fg=pal["sub"], font=app.F["small"],
                             bd=0, anchor="w", justify="left", wraplength=px(420))
        if desc:
            self.title.grid(row=0, column=1, sticky="sew", pady=(px(11), 0))
            self.desc.grid(row=1, column=1, sticky="new", pady=(px(1), px(11)))
        else:
            self.title.grid(row=0, column=1, rowspan=2, sticky="ew", pady=px(14))
        self.right = tk.Frame(c, bg=pal["card"])
        self.right.grid(row=0, column=2, rowspan=2, padx=(px(12), px(16)))
        _page_of(parent).cards.append(self)

    def set_desc(self, text):
        if self.desc.cget("text") != text:
            self.desc.config(text=text)

    def set_image(self, image):
        self.icon.config(image=image, text="")

    def wrap(self, body_w):
        body_w -= 2      # the border
        px = self.ui.app.px
        avail = (body_w - px(24) - 2 - self.icon.winfo_reqwidth() - px(30)
                 - self.right.winfo_reqwidth() - px(28))
        w = max(px(140), avail)
        for lbl in (self.title, self.desc):
            if abs(int(float(str(lbl.cget("wraplength")) or 0)) - w) > 2:
                lbl.config(wraplength=w)

    def clickable(self, command):
        for w in (self.frame, self.icon, self.title, self.desc, self.right,
                  *self.right.winfo_children()):
            w.config(cursor="hand2")
            w.bind("<Button-1>", lambda e: command())


class SettingsWindow:
    def __init__(self, app, page="general"):
        self.app = app
        self.page = page if page in PAGES else "general"
        self.win = None
        self._history = (0.0, [])
        self.build()

    # ---------------------------------------------------------------- shell
    def build(self):
        app, px, pal = self.app, self.app.px, self.app.pal
        geo = None
        hidden = False
        if self.win is not None:
            geo = self.win.geometry()
            hidden = self.win.state() == "withdrawn"
            self.win.destroy()
        win = self.win = tk.Toplevel(app.root, bg=pal["bg"])
        win.withdraw()
        win.title(f"{APP_NAME} · {t('settings')}")
        win.minsize(px(680), px(480))
        if geo:
            win.geometry(geo)
        else:
            w, h = px(900), px(660)
            l, tp, r, b = winsys.work_area()
            win.geometry(f"{w}x{h}+{l + (r - l - w) // 2}+{tp + (b - tp - h) // 2}")
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda e: self.close())

        nav = tk.Frame(win, bg=pal["bg"], width=px(240))
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        top = tk.Frame(nav, bg=pal["bg"])
        top.pack(fill="x", padx=px(18), pady=(px(20), px(16)))
        self.logo = tk.PhotoImage(data=png_b64(app_icon(px(28))))
        tk.Label(top, image=self.logo, bg=pal["bg"], bd=0).pack(side="left")
        tk.Label(top, text=APP_NAME, bg=pal["bg"], fg=pal["fg"], font=app.F["title"],
                 bd=0).pack(side="left", padx=(px(10), 0))
        self.nav_items = {key: self._nav_item(nav, key) for key in PAGES}

        right = tk.Frame(win, bg=pal["bg"])
        right.pack(side="left", fill="both", expand=True)
        self.title = tk.Label(right, text="", bg=pal["bg"], fg=pal["fg"], bd=0, anchor="w",
                              font=(app.F["big"][0], app.F["big"][1] + 5, "bold"))
        self.title.pack(fill="x", padx=(px(12), px(24)), pady=(px(22), px(12)))
        holder = tk.Frame(right, bg=pal["bg"])
        holder.pack(fill="both", expand=True, pady=(0, px(12)))
        self.canvas = tk.Canvas(holder, bg=pal["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True, padx=(0, px(14)))
        # overlay scrollbar: shown only when needed and it never changes the layout
        self.sb = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._yset)
        self.root_body = self.canvas     # pages live directly on the canvas
        self.pages = {}          # key -> page frame (with .cards .paras .update .item)
        self.body = None
        self.canvas.bind("<Configure>", self._on_resize)
        win.bind("<MouseWheel>", self._wheel)
        self._relayout_job = None

        self.show_page(self.page)
        win.update_idletasks()
        self._relayout()
        if not hidden:        # rebuilt (language/theme) while closed: stay closed
            win.deiconify()
            self._style_titlebar()
            self.focus()
        self.win.after(1000, self._tick)

    def _yset(self, lo, hi):
        self.sb.set(lo, hi)
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.sb.place_forget()
        elif not self.sb.winfo_ismapped():
            self.sb.place(relx=1.0, x=-self.app.px(2), rely=0, relheight=1.0, anchor="ne")

    def _on_resize(self, e):
        for page in self.pages.values():
            self.canvas.itemconfigure(page.item, width=e.width)
        if self._relayout_job:
            self.win.after_cancel(self._relayout_job)
        self._relayout_job = self.win.after(40, self._relayout)

    def _relayout(self):
        """Word wrap computed from the real width: no text 'dancing'."""
        self._relayout_job = None
        if not self.alive():
            return
        w = self.canvas.winfo_width()
        if w < 50:
            return
        page = self.body
        if page is None:
            return
        page.width = w
        for card in page.cards:
            if card.frame.winfo_exists():
                card.wrap(w)
        for lbl in page.paras:
            if lbl.winfo_exists():
                lbl.config(wraplength=max(100, w - self.app.px(28)))

    def _style_titlebar(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            winsys.dwm_style(hwnd, self.app.dark)
        except Exception:
            pass

    def _fit(self, page):
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(),
                                            page.winfo_reqheight()))

    def _wheel(self, e):
        if self.body is not None and self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units")

    def focus(self):
        if self.win.state() in ("withdrawn", "iconic"):
            self.win.deiconify()
            self._style_titlebar()
        self.win.lift()
        self.win.focus_force()

    def close(self):
        """Hide (reopening is then instant); destroy() is for language/theme changes."""
        if self.alive():
            self.win.withdraw()

    def destroy(self):
        if self.win is not None:
            self.win.destroy()
            self.win = None
        self.app.settings_win = None

    def visible(self):
        return self.alive() and self.win.state() == "normal"

    def alive(self):
        return self.win is not None and self.win.winfo_exists()

    def _nav_item(self, parent, key):
        app, px, pal = self.app, self.app.px, self.app.pal
        row = tk.Frame(parent, bg=pal["bg"], cursor="hand2")
        row.pack(fill="x", padx=px(8), pady=px(1))
        bar = tk.Frame(row, bg=pal["bg"], width=px(3), height=px(16))
        bar.pack(side="left", padx=(px(2), 0))
        ic = tk.Label(row, text=G[key], font=(app.icon_font, 12), bg=pal["bg"], fg=pal["fg"],
                      bd=0)
        ic.pack(side="left", padx=(px(10), px(12)), pady=px(9))
        lb = tk.Label(row, text=t(f"nav_{key}"), font=app.F["body"], bg=pal["bg"],
                      fg=pal["fg"], bd=0, anchor="w")
        lb.pack(side="left", fill="x", expand=True)
        parts = (row, ic, lb)

        def paint(hover=False):
            sel = key == self.page
            bg = pal["hover"] if (sel or hover) else pal["bg"]
            for w in parts:
                w.config(bg=bg)
            bar.config(bg=pal["accent"] if sel else bg)
        for w in parts:
            w.bind("<Button-1>", lambda e: self.show_page(key))
            w.bind("<Enter>", lambda e: paint(True))
            w.bind("<Leave>", lambda e: paint(False))
        row.paint = paint
        return row

    def show_page(self, key, rebuild=False):
        """Pages are built the first time and then kept: switching is instant."""
        if key not in PAGES:
            key = "general"
        old = self.body
        if old is not None:
            old.scroll_y = self.canvas.yview()[0]
            self.canvas.itemconfigure(old.item, state="hidden")
        if rebuild and key in self.pages:
            gone = self.pages.pop(key)
            self.canvas.delete(gone.item)
            gone.destroy()
        self.page = key
        for item in self.nav_items.values():
            item.paint()
        self.title.config(text=t(f"nav_{key}"))
        page = self.pages.get(key)
        fresh = page is None
        if fresh:
            page = tk.Frame(self.canvas, bg=self.app.pal["bg"])
            page.cards, page.paras, page.update, page.width, page.scroll_y = [], [], None, 0, 0.0
            page.item = self.canvas.create_window(0, 0, window=page, anchor="nw",
                                                  width=max(1, self.canvas.winfo_width()))
            page.bind("<Configure>", lambda e, pg=page: self.body is pg and self._fit(pg))
            self.pages[key] = page
        self.canvas.itemconfigure(page.item, state="normal")
        self.body = page
        if fresh:
            self._update = None          # page function that refreshes values
            getattr(self, f"_page_{key}")()
            page.update = self._update
        else:
            self._update = page.update
            if self._update:
                self._update()           # values may have changed meanwhile
        if fresh or page.width != self.canvas.winfo_width():
            self.win.update_idletasks()
            if self.win.winfo_ismapped():
                self._relayout()
        self._fit(page)
        self.canvas.yview_moveto(0 if fresh else page.scroll_y)

    def _tick(self):
        if not self.alive():
            return
        if self.visible():            # hidden window: no work at all
            self.app.engine.apps_wanted = time.monotonic()   # keep the app list fresh
            try:
                if self._update:
                    self._update()
            except tk.TclError:
                pass
        self.win.after(1000, self._tick)

    def _prune(self):
        page = self.body
        page.cards[:] = [c for c in page.cards if c.frame.winfo_exists()]

    def _keep_scroll(self, rebuild):
        y = self.canvas.yview()[0]
        rebuild()
        self.win.update_idletasks()
        self._relayout()
        self.canvas.yview_moveto(y)

    # ---------------------------------------------------------------- widgets
    def para(self, text, kind="sub", parent=None, pady=(0, 0)):
        app, px, pal = self.app, self.app.px, self.app.pal
        fg = {"sub": pal["sub"], "body": pal["fg"], "warn": pal["cut"]}[kind]
        font = app.F["body"] if kind == "body" else app.F["small"]
        lbl = tk.Label(parent or self.body, text=text, bg=pal["bg"], fg=fg, font=font, bd=0,
                       justify="left", anchor="w", wraplength=px(480))
        lbl.pack(fill="x", padx=px(14), pady=pady)
        _page_of(lbl).paras.append(lbl)
        return lbl

    def heading(self, text, parent=None):
        px = self.app.px
        lbl = tk.Label(parent or self.body, text=text, bg=self.app.pal["bg"],
                       fg=self.app.pal["fg"], bd=0, anchor="w",
                       font=(self.app.F["body"][0], self.app.F["body"][1], "bold"))
        lbl.pack(fill="x", padx=px(14), pady=(px(18), px(8)))
        return lbl

    def card(self, glyph, title, desc="", parent=None, image=None):
        return Card(self, parent or self.body, glyph, title, desc, image)

    def switch(self, card, value, command):
        var = tk.BooleanVar(value=value)
        cb = ttk.Checkbutton(card.right, style="Card.Switch.TCheckbutton", variable=var,
                             command=lambda: command(var.get()))
        cb.pack(side="right")
        cb.var = var
        return cb

    @staticmethod
    def sync(var, value):
        if var.get() != value:
            var.set(value)

    def link_card(self, glyph, title, desc, command):
        c = self.card(glyph, title, desc)
        tk.Label(c.right, text=G["open"], font=(self.app.icon_font, 10),
                 bg=self.app.pal["card"], fg=self.app.pal["sub"], bd=0).pack(side="right")
        c.clickable(command)
        return c

    # ---------------------------------------------------------------- general
    def _page_general(self):
        app, s, px = self.app, self.app.settings, self.app.px
        c = self.card(G["power"], t("gen_enabled"), t("gen_enabled_desc"))
        on = self.switch(c, s["enabled"], app.set_enabled)

        pc = self.card(G["level"], t("intensity"), t(f"desc_{s['preset']}"))
        preset = tk.StringVar(value=s["preset"])

        def pick():
            app.set_preset(preset.get())
            pc.set_desc(t(f"desc_{preset.get()}"))
        seg = tk.Frame(pc.right, bg=app.pal["card"])
        seg.pack(side="right")
        for i, key in enumerate(PRESET_ORDER):
            ttk.Radiobutton(seg, text=t(f"preset_{key}"), value=key, variable=preset,
                            style="Card.Toggle.TButton", command=pick
                            ).grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else px(4), 0))
            seg.columnconfigure(i, uniform="seg")

        c = self.card(G["start"], t("autostart"), t("gen_autostart_desc"))
        auto = self.switch(c, winsys.autostart_enabled(), app.set_autostart)

        c = self.card(G["language"], t("gen_language"), t("gen_language_desc"))
        names, codes = list(i18n.LANGUAGES.values()), list(i18n.LANGUAGES)
        lang = tk.StringVar(value=i18n.LANGUAGES.get(i18n.current(), names[0]))
        cb = ttk.Combobox(c.right, textvariable=lang, values=names, state="readonly", width=16,
                          style="Card.TCombobox")
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda e: (
            cb.selection_clear(), app.set_language(codes[names.index(lang.get())])))

        info = {"card": None}

        def update():   # changes made from the panel or the tray menu
            show = s["enabled"] and app.engine.headroom_short
            if show and info["card"] is None:
                info["card"] = self.card(G["info"], t("headroom_title"), t("warn_headroom"))
                self.win.update_idletasks()
                self._relayout()
            elif not show and info["card"] is not None:
                info["card"].outer.destroy()
                info["card"] = None
            self.sync(on.var, s["enabled"])
            if preset.get() != s["preset"]:
                preset.set(s["preset"])
                pc.set_desc(t(f"desc_{s['preset']}"))
            self.sync(auto.var, winsys.autostart_enabled())
        self._update = update
        update()

    # ---------------------------------------------------------------- devices
    def _page_devices(self):
        app, eng, s, px = self.app, self.app.engine, self.app.settings, self.app.px
        now = self.para("", pady=(0, px(10)))
        mode = tk.StringVar(value=s["device_mode"])

        def set_mode():
            if mode.get() != s["device_mode"]:
                app.set_devices(mode.get(), s["devices"])
                self.show_page("devices", rebuild=True)
        auto = self.card(G["auto"], t("dev_auto_title"), " ")
        manual_card = self.card(G["pick"], t("dev_manual_title"), t("dev_manual_desc"))
        for c, value in ((auto, "auto"), (manual_card, "manual")):
            ttk.Radiobutton(c.right, value=value, variable=mode, style="Card.TRadiobutton",
                            command=set_mode).pack(side="right")
            c.clickable(lambda v=value: (mode.set(v), set_mode()))

        self.heading(t("dev_list"))
        box = tk.Frame(self.body, bg=self.app.pal["bg"])
        box.pack(fill="x")
        warn = self.para("", "warn", pady=(px(6), 0))
        hint = self.para(t("dev_hint"), pady=(px(10), 0))
        warn.pack_forget()
        rows = {}

        def toggle(dev_id, name, on):
            ids = [i for i in s["devices"] if i != dev_id]
            if on:
                ids.append(dev_id)
                s["device_names"][dev_id] = name
            app.set_devices("manual", ids)

        def listed():
            connected = [(i, n, f, True) for i, n, f in eng.devices]
            ids = {i for i, *_ in connected}
            gone = [(i, s["device_names"].get(i, "?"), 0, False)
                    for i in s["devices"] if i not in ids]
            return connected + gone

        def build_rows():
            for w in box.winfo_children():
                w.destroy()
            self._prune()
            rows.clear()
            manual = s["device_mode"] == "manual"
            for dev_id, name, ff, online in listed():
                main, _adapter = split_device_name(name)
                c = self.card(G[FORM_ICON.get(ff, "speakers")], short(main, 60), " ",
                              parent=box)
                sw = None
                if manual:
                    sw = self.switch(c, dev_id in s["devices"],
                                     lambda v, i=dev_id, n=name: toggle(i, n, v))
                rows[dev_id] = (c, sw, name, online)
            update_rows()

        def update_rows():
            for dev_id, (c, sw, name, online) in rows.items():
                adapter = split_device_name(name)[1]
                tags = [adapter] if adapter else []
                if dev_id == eng.default_id:
                    tags.append(t("dev_default_tag"))
                if not online:
                    tags.append(t("dev_off_tag"))
                if dev_id in eng.device_ids:
                    tags.append(t("dev_active_tag"))
                c.set_desc(" · ".join(tags))
                if sw is not None:
                    self.sync(sw.var, dev_id in s["devices"])

        state = {"sig": None}

        def update():
            names = [split_device_name(n)[0] for n in eng.device.split(" + ") if n]
            now.config(text=t("dev_now", name=" + ".join(names) or "—"))
            auto.set_desc(t("dev_auto_desc",
                            name=split_device_name(eng.default_name or "—")[0]))
            self.sync(mode, s["device_mode"])
            connected = {i for i, *_ in eng.devices}
            manual = s["device_mode"] == "manual"
            show = manual and not any(i in connected for i in s["devices"])
            if show and not warn.winfo_ismapped():
                warn.config(text=t("dev_none_warn"))
                warn.pack(fill="x", padx=px(14), pady=(px(6), 0), before=hint)
            elif not show and warn.winfo_ismapped():
                warn.pack_forget()
            sig = (tuple((i, n, o) for i, n, _f, o in listed()), s["device_mode"])
            if sig != state["sig"]:
                state["sig"] = sig
                self._keep_scroll(build_rows)
            else:
                update_rows()
        self._update = update
        update()

    # ---------------------------------------------------------------- apps
    def _history_apps(self):
        from .appinfo import AUDIO_CACHE
        if "apps" in AUDIO_CACHE and self._history[0] == 0.0:
            self._history = AUDIO_CACHE["apps"]
        stamp, apps = self._history
        if time.monotonic() - stamp > HISTORY_TTL:
            apps = audio_history()
            self._history = (time.monotonic(), apps)
        return apps

    def _app_rows(self):
        """{low: row} of every app: Windows audio history, apps seen by Levelo,
        apps open now (any device), apps with a rule."""
        eng, s = self.app.engine, self.app.settings
        rows = {}

        def put(name, path="", playing=False, present=False):
            low = name.lower()
            r = rows.get(low)
            if r is None:
                rows[low] = dict(low=low, exe=name, path=path, playing=playing,
                                 present=present)
            else:
                r["path"] = r["path"] or path
                r["playing"] |= playing
                r["present"] |= present
        for name, path in self._history_apps():
            put(name, path)
        for low, info in s["seen_apps"].items():
            put(info.get("exe", low), info.get("path", ""))
        for low in s["app_rules"]:
            put(low)
        for name, path, playing in eng.other_apps:
            put(name, path, playing, True)
        for a in eng.apps:
            put(a[0], a[4], a[3], True)
        return rows

    def _status(self, r):
        s = self.app.settings
        if r["playing"]:
            status = t("app_playing")
        elif r["present"]:
            status = t("app_open")
        else:
            status = t("app_idle")
        call = t("app_call") if r["low"] in CALL_APPS else ""
        exe = r["exe"] if r["exe"] != "System" else ""
        excluded = (not s["app_rules"][r["low"]]) if r["low"] in s["app_rules"] else \
            (s["call_protect"] and r["low"] in CALL_APPS)
        return " · ".join(x for x in (exe, call, status) if x), not excluded

    def _page_apps(self):
        app, px, pal = self.app, self.app.px, self.app.pal
        self.para(t("apps_desc"), pady=(0, px(12)))
        bar = tk.Frame(self.body, bg=pal["bg"])
        bar.pack(fill="x", padx=px(12), pady=(0, px(10)))
        search = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=search, width=30)
        ent.pack(side="left")
        ph = tk.Label(bar, text=t("apps_search"), bg=pal["card"], fg=pal["sub"],
                      font=app.F["body"], bd=0, cursor="xterm")

        def placeholder(*_a):
            if search.get() or self.win.focus_get() is ent:
                ph.place_forget()
            else:
                ph.place(in_=ent, x=px(11), rely=0.5, anchor="w")
                ph.lift()
        ph.bind("<Button-1>", lambda e: ent.focus_set())
        ent.bind("<FocusIn>", placeholder)
        ent.bind("<FocusOut>", placeholder)
        ttk.Button(bar, text=t("apps_add"), command=self._add_app).pack(side="right")
        box = tk.Frame(self.body, bg=pal["bg"])
        box.pack(fill="x")
        empty = self.para("", pady=(px(8), 0))
        rows = {}           # low -> (card, switch)
        state = {"keys": None, "job": None, "icons": [], "gen": 0, "building": False}

        def visible():
            q = search.get().strip().lower()
            out = []
            for low, r in self._app_rows().items():
                r["title"] = t("app_system") if r["exe"] == "System" else \
                    app_title(r["exe"], r["path"], FRIENDLY)
                if not q or q in r["title"].lower() or q in low:
                    out.append(r)
            # fixed alphabetical order: rows never jump around
            out.sort(key=lambda r: (r["exe"] != "System", r["title"].casefold(), r["low"]))
            return out

        page = self.body
        box.photos = []

        def build(items):
            state["gen"] += 1
            gen = state["gen"]
            for w in box.winfo_children():
                w.destroy()
            self._prune()
            box.photos = []
            rows.clear()
            todo = []
            for label, group in (("apps_now", [r for r in items if r["present"]]),
                                 ("apps_other", [r for r in items if not r["present"]])):
                if group:
                    todo.append(("head", label))
                    todo += [("row", r) for r in group]
            empty.config(text="" if items else t("apps_empty"))
            state["icons"] = []
            state["building"] = True

            def step():
                if gen != state["gen"] or not box.winfo_exists():
                    return
                new = []
                count = 0
                while todo and count < 12:
                    kind, x = todo.pop(0)
                    if kind == "head":
                        h = self.heading(t(x), parent=box)
                        if not box.winfo_children()[:-1]:
                            h.pack_configure(pady=(px(4), px(8)))
                    else:
                        c, icon = add_row(x)
                        new.append(c)
                        state["icons"] += icon
                        count += 1
                box.update_idletasks()
                w = self.canvas.winfo_width()
                for c in new:
                    c.wrap(w)
                if todo:
                    self.win.after(1, step)
                else:
                    state["building"] = False
                    load_icons()
            step()

        def add_row(r):
            desc, on = self._status(r)
            glyph = G["system"] if r["exe"] == "System" else G["app"]
            c = self.card(glyph, short(r["title"], 60), desc, parent=box)
            sw = self.switch(c, on, lambda v, n=r["exe"]: app.set_app_rule(n, v))
            rows[r["low"]] = (c, sw)
            return c, ([(c, r["path"])] if r["path"] else [])

        def load_icons():
            """Real app icons, a few at a time so the window never freezes."""
            batch, state["icons"] = state["icons"][:8], state["icons"][8:]
            for c, path in batch:
                if not c.frame.winfo_exists():
                    continue
                pil = exe_icon(path, px(24))
                if pil is not None:
                    img = tk.PhotoImage(data=png_b64(pil))
                    box.photos.append(img)
                    c.set_image(img)
            if state["icons"]:
                self.win.after(15, load_icons)

        def update():
            if state["building"] or self.body is not page:
                return
            items = visible()
            keys = [(r["low"], r["present"]) for r in items]
            if keys != state["keys"]:
                state["keys"] = keys
                self._keep_scroll(lambda: build(items))
                return
            for r in items:
                c, sw = rows[r["low"]]
                desc, on = self._status(r)
                c.set_desc(desc)
                self.sync(sw.var, on)

        def on_search(*_a):
            placeholder()
            if state["job"]:
                self.win.after_cancel(state["job"])
            state["job"] = self.win.after(150, update)
        search.trace_add("write", on_search)
        self.win.after(60, placeholder)
        self._update = update
        self._refresh_apps = update
        update()

    def _add_app(self):
        path = filedialog.askopenfilename(
            parent=self.win, title=t("apps_add"),
            filetypes=[(t("apps_add_filter"), "*.exe")])
        if not path:
            return
        path = os.path.normpath(path)
        name = os.path.basename(path)
        self.app.settings["seen_apps"][name.lower()] = {"exe": name, "path": path,
                                                       "seen": time.time()}
        self.app.set_app_rule(name, False)    # added to be left alone
        if self.page == "apps":
            self._refresh_apps()

    # ---------------------------------------------------------------- calls
    def _page_calls(self):
        app, s, px = self.app, self.app.settings, self.app.px
        c = self.card(G["calls"], t("calls_protect"), t("calls_desc"))
        sw = self.switch(c, s["call_protect"], app.set_call_protect)
        self.card(G["apps"], t("calls_list_title"), ", ".join(CALL_NAMES))
        self.card(G["info"], t("calls_browser_title"), t("calls_browser"))
        self.para(t("calls_custom"), pady=(px(10), 0))
        self._update = lambda: self.sync(sw.var, s["call_protect"])

    # ---------------------------------------------------------------- about
    def _page_about(self):
        app, px, pal = self.app, self.app.px, self.app.pal
        head = tk.Frame(self.body, bg=pal["bg"])
        head.pack(fill="x", padx=px(14), pady=(0, px(16)))
        self.big_logo = tk.PhotoImage(data=png_b64(app_icon(px(56))))
        tk.Label(head, image=self.big_logo, bg=pal["bg"], bd=0).pack(side="left")
        col = tk.Frame(head, bg=pal["bg"])
        col.pack(side="left", padx=(px(14), 0))
        tk.Label(col, text=APP_NAME, bg=pal["bg"], fg=pal["fg"], font=app.F["big"],
                 bd=0).pack(anchor="w")
        for text in (t("about_version", v=__version__ + (" beta" if BETA else "")),
                     t("about_license")):
            tk.Label(col, text=text, bg=pal["bg"], fg=pal["sub"], font=app.F["small"],
                     bd=0).pack(anchor="w")

        box = tk.Frame(self.body, bg=pal["bg"])
        box.pack(fill="x")
        state = {"update": object()}

        def update():
            if app.update == state["update"]:
                return
            state["update"] = app.update
            for w in box.winfo_children():
                w.destroy()
            self._prune()
            if app.update:
                c = self.card(G["update"], t("about_update", v=app.update[0]),
                              t("about_update_desc"), parent=box)
                ttk.Button(c.right, text=t("about_download"), style="Card.Accent.TButton",
                           command=lambda: winsys.open_url(app.update[1])).pack(side="right")
            else:
                self.card(G["update"], t("about_latest"), t("about_latest_desc"), parent=box)
            if self.win.winfo_ismapped():
                self.win.update_idletasks()
                self._relayout()
        update()
        self._update = update

        self.heading(t("about_links"))
        if app.website:
            self.link_card(G["web"], t("menu_website"), app.website,
                           lambda: winsys.open_url(app.website))
        self.link_card(G["bug"], t("report_bug"), t("report_bug_desc"), app.report_bug)
        self.link_card(G["folder"], t("open_logs"), DATA_DIR,
                       lambda: winsys.open_url(DATA_DIR))
        if app.donate_url:
            self.link_card(G["heart"], t("menu_donate"), t("about_donate_desc"),
                           lambda: winsys.open_url(app.donate_url))


def prewarm_apps():
    """Fill the name/icon caches (pure Win32 calls, safe in a thread)."""
    try:
        from .appinfo import AUDIO_CACHE
        apps = audio_history()
        AUDIO_CACHE["apps"] = (time.monotonic(), apps)
        for name, path in apps:
            app_title(name, path, FRIENDLY)
    except Exception:
        pass


def remember_apps(settings, apps, other=()):
    """Add the apps seen now to settings['seen_apps']. Returns True if changed."""
    seen = settings["seen_apps"]
    changed = False
    now = time.time()
    pairs = [(a[0], a[4]) for a in apps] + [(o[0], o[1]) for o in other]
    for name, path in pairs:
        low = name.lower()
        old = seen.get(low)
        if old is None or (path and old.get("path") != path) or now - old.get("seen", 0) > 86400:
            seen[low] = {"exe": name, "path": path or (old or {}).get("path", ""), "seen": now}
            changed = True
    if len(seen) > SEEN_MAX:
        keep = sorted(seen.items(), key=lambda kv: kv[1].get("seen", 0), reverse=True)
        rules = settings["app_rules"]
        settings["seen_apps"] = dict(kv for i, kv in enumerate(keep)
                                     if i < SEEN_MAX or kv[0] in rules)
        changed = True
    return changed
