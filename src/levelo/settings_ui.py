"""Settings window, laid out like the Windows 11 Settings app: navigation on the
left, "setting cards" on the right (icon, title, description, control)."""

import ctypes
import re
import time
import os
import tkinter as tk
from tkinter import filedialog, ttk

from . import APP_NAME, BETA, __version__, i18n, winsys
from .appinfo import exe_icon, file_description
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
         info="", warn="", system="")

# KSJACK form factor of an output device -> icon
FORM_ICON = {1: "speakers", 3: "headphones", 4: "headset", 5: "headset", 9: "display",
             8: "display"}

PAGES = ("general", "devices", "apps", "calls", "about")
PRESET_ORDER = ("light", "normal", "night")
SEEN_MAX = 60

# Display names of the call apps that are recognised (shown on the Calls page)
CALL_NAMES = ("Discord", "Microsoft Teams", "Zoom", "Skype", "Slack", "WhatsApp",
              "Telegram", "Signal", "Viber", "TeamSpeak", "Mumble", "Webex",
              "RingCentral", "GoTo Meeting", "Element", "Guilded", "Beeper")


# Store apps whose exe has no readable description
FRIENDLY = {"whatsapp.root.exe": "WhatsApp", "whatsapp.exe": "WhatsApp",
            "ms-teams.exe": "Microsoft Teams", "msteams.exe": "Microsoft Teams",
            "spotify.exe": "Spotify", "netflix.exe": "Netflix", "music.ui.exe": "Media Player",
            "microsoft.media.player.exe": "Media Player", "video.ui.exe": "Film e TV",
            "phoneexperiencehost.exe": "Phone Link", "telegram.exe": "Telegram"}


def split_device_name(name):
    """'Speakers (2- Realtek Audio)' -> ('Speakers', 'Realtek Audio'), like the
    two lines of the Windows sound settings."""
    if name.endswith(")") and " (" in name:
        i = name.index(" (")
        return name[:i], re.sub(r"^\d+-\s*", "", name[i + 2:-1])
    return name, ""


def short(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"


class SettingsWindow:
    def __init__(self, app, page="general"):
        self.app = app
        self.page = page if page in PAGES else "general"
        self.win = None
        self._sig = None
        self._photos = []
        self.build()

    # ---------------------------------------------------------------- shell
    def build(self):
        app, px, pal = self.app, self.app.px, self.app.pal
        geo = None
        if self.win is not None:
            geo = self.win.geometry()
            self.win.destroy()
        win = self.win = tk.Toplevel(app.root)
        win.withdraw()
        win.title(f"{APP_NAME} · {t('settings')}")
        win.configure(bg=pal["bg"])
        win.minsize(px(640), px(460))
        if geo:
            win.geometry(geo)
        else:
            w, h = px(860), px(620)
            l, tp, r, b = winsys.work_area()
            win.geometry(f"{w}x{h}+{l + (r - l - w) // 2}+{tp + (b - tp - h) // 2}")
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda e: self.close())

        nav = tk.Frame(win, bg=pal["bg"], width=px(232))
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        top = tk.Frame(nav, bg=pal["bg"])
        top.pack(fill="x", padx=px(16), pady=(px(18), px(18)))
        self.logo = tk.PhotoImage(data=png_b64(app_icon(px(28))))
        tk.Label(top, image=self.logo, bg=pal["bg"], bd=0).pack(side="left")
        tk.Label(top, text=APP_NAME, bg=pal["bg"], fg=pal["fg"], font=app.F["title"],
                 bd=0).pack(side="left", padx=(px(10), 0))
        self.nav_items = {}
        for key in PAGES:
            self.nav_items[key] = self._nav_item(nav, key)

        right = tk.Frame(win, bg=pal["bg"])
        right.pack(side="left", fill="both", expand=True)
        self.title = tk.Label(right, text="", bg=pal["bg"], fg=pal["fg"], bd=0, anchor="w",
                              font=(app.F["big"][0], app.F["big"][1] + 5, "bold"))
        self.title.pack(fill="x", padx=(px(12), px(28)), pady=(px(22), px(10)))
        holder = tk.Frame(right, bg=pal["bg"])
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=pal["bg"], highlightthickness=0, bd=0)
        sb = self.sb = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)

        def yset(lo, hi):
            sb.set(lo, hi)
            if float(lo) <= 0.0 and float(hi) >= 1.0:
                sb.pack_forget()
            elif not sb.winfo_ismapped():
                sb.pack(side="right", fill="y", before=self.canvas)
        self.canvas.configure(yscrollcommand=yset)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=pal["bg"])
        self._body_id = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._body_id, width=e.width - px(16)))
        win.bind("<MouseWheel>", self._wheel)

        self.show_page(self.page)
        win.update_idletasks()
        win.deiconify()
        self._style_titlebar()
        self.focus()
        self._tick()

    def _style_titlebar(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            winsys.dwm_style(hwnd, self.app.dark)
        except Exception:
            pass

    def _wheel(self, e):
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units")

    def focus(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def close(self):
        if self.win is not None:
            self.win.destroy()
            self.win = None
        self.app.settings_win = None

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

    def show_page(self, key):
        self.page = key
        for k, item in self.nav_items.items():
            item.paint()
        self.title.config(text=t(f"nav_{key}"))
        for w in self.body.winfo_children():
            w.destroy()
        self._photos = []
        self._sig = None
        getattr(self, f"_page_{key}")()
        self.canvas.yview_moveto(0)

    def _tick(self):
        """Keep the page in sync with the engine and with changes made elsewhere."""
        if not self.alive():
            return
        sig = self._signature()
        if sig != self._sig:
            if self._sig is not None:
                y = self.canvas.yview()[0]
                self._rebuild_dynamic()
                self.canvas.update_idletasks()
                self.canvas.yview_moveto(y)
            self._sig = sig
        self.win.after(1000, self._tick)

    def _signature(self):
        eng, s = self.app.engine, self.app.settings
        if self.page == "general":
            return (s["enabled"], s["preset"], winsys.autostart_enabled(), i18n.current())
        if self.page == "devices":
            return (eng.devices, eng.default_id, eng.device_ids, s["device_mode"],
                    tuple(s["devices"]))
        if self.page == "apps":
            return (tuple(a[:4] for a in eng.apps), eng.other_apps, s["call_protect"],
                    tuple(sorted(s["app_rules"].items())), len(s["seen_apps"]))
        if self.page == "calls":
            return (s["call_protect"],)
        if self.page == "about":
            return (self.app.update,)
        return None

    def _rebuild_dynamic(self):
        if self.page == "apps":
            self._fill_apps()
        else:
            self.show_page(self.page)

    # ---------------------------------------------------------------- widgets
    def text(self, parent, text, kind="sub", wrap=True, **kw):
        app, pal = self.app, self.app.pal
        bg = kw.pop("bg", pal["bg"])
        fg = {"sub": pal["sub"], "body": pal["fg"], "warn": pal["cut"]}[kind]
        font = app.F["small"] if kind in ("sub", "warn") else app.F["body"]
        lbl = tk.Label(parent, text=text, bg=bg, fg=fg, font=font, bd=0, justify="left",
                       anchor="w", **kw)
        if wrap:
            lbl.bind("<Configure>", lambda e: lbl.config(wraplength=max(50, e.width - 4)))
        return lbl

    def heading(self, text):
        px = self.app.px
        lbl = self.text(self.body, text, "body", wrap=False)
        lbl.config(font=(self.app.F["body"][0], self.app.F["body"][1], "bold"))
        lbl.pack(fill="x", padx=(px(12), 0), pady=(px(18), px(6)))

    def card(self, glyph, title, desc="", parent=None, image=None, indent=0):
        """A Windows 11 settings card. Returns (card, right side for the control,
        description label)."""
        app, px, pal = self.app, self.app.px, self.app.pal
        outer = tk.Frame(parent or self.body, bg=pal["border"])
        outer.pack(fill="x", padx=(px(12) + indent, px(12)), pady=(0, px(4)))
        c = tk.Frame(outer, bg=pal["card"])
        c.pack(fill="x", padx=1, pady=1)
        if image is not None:
            tk.Label(c, image=image, bg=pal["card"], bd=0, width=px(24)).pack(
                side="left", padx=(px(16), px(14)), pady=px(14))
        else:
            tk.Label(c, text=glyph, font=(app.icon_font, 14), bg=pal["card"], fg=pal["fg"],
                     bd=0, width=2).pack(side="left", padx=(px(14), px(10)), pady=px(14))
        right = tk.Frame(c, bg=pal["card"])
        right.pack(side="right", padx=(px(8), px(16)))
        mid = tk.Frame(c, bg=pal["card"])
        mid.pack(side="left", fill="x", expand=True, pady=px(10))
        self.text(mid, title, "body", bg=pal["card"]).pack(fill="x")
        d = None
        if desc:
            d = self.text(mid, desc, "sub", bg=pal["card"])
            d.pack(fill="x", pady=(px(1), 0))
        return c, right, d

    def switch(self, parent, value, command, enabled=True):
        var = tk.BooleanVar(value=value)
        cb = ttk.Checkbutton(parent, style="Card.Switch.TCheckbutton", variable=var,
                             command=lambda: command(var.get()))
        if not enabled:
            cb.state(["disabled"])
        cb.pack(side="right")
        cb.var = var
        return cb

    def link_card(self, glyph, title, desc, command):
        c, right, _d = self.card(glyph, title, desc)
        tk.Label(right, text="", font=(self.app.icon_font, 10), bg=self.app.pal["card"],
                 fg=self.app.pal["sub"], bd=0).pack(side="right")
        for w in [c] + list(c.winfo_children()):
            w.config(cursor="hand2")
            w.bind("<Button-1>", lambda e: command())
            for ww in w.winfo_children():
                ww.config(cursor="hand2")
                ww.bind("<Button-1>", lambda e: command())

    # ---------------------------------------------------------------- general
    def _page_general(self):
        app, s, px = self.app, self.app.settings, self.app.px
        _c, r, _d = self.card(G["power"], t("gen_enabled"), t("gen_enabled_desc"))
        self.switch(r, s["enabled"], app.set_enabled)

        _c, r, d = self.card(G["level"], t("intensity"), t(f"desc_{s['preset']}"))
        var = tk.StringVar(value=s["preset"])

        def pick():
            app.set_preset(var.get())
            d.config(text=t(f"desc_{var.get()}"))
        for key in PRESET_ORDER:
            ttk.Radiobutton(r, text=t(f"preset_{key}"), value=key, variable=var,
                            style="Toggle.TButton", command=pick, width=9
                            ).pack(side="left", padx=(px(4), 0))

        _c, r, _d = self.card(G["start"], t("autostart"), t("gen_autostart_desc"))
        self.switch(r, winsys.autostart_enabled(), app.set_autostart)

        _c, r, _d = self.card(G["language"], t("gen_language"), t("gen_language_desc"))
        names = list(i18n.LANGUAGES.values())
        codes = list(i18n.LANGUAGES)
        lang = tk.StringVar(value=i18n.LANGUAGES.get(i18n.current(), names[0]))
        cb = ttk.Combobox(r, textvariable=lang, values=names, state="readonly", width=14)
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>",
                lambda e: app.set_language(codes[names.index(lang.get())]))

    # ---------------------------------------------------------------- devices
    def _page_devices(self):
        app, eng, s, px = self.app, self.app.engine, self.app.settings, self.app.px
        now = split_device_name(eng.device)[0] if eng.device and " + " not in eng.device \
            else eng.device
        self.text(self.body, t("dev_now", name=now or "—"), "sub").pack(
            fill="x", padx=(px(12), px(12)), pady=(0, px(10)))

        mode = tk.StringVar(value=s["device_mode"])

        def set_mode():
            app.set_devices(mode.get(), s["devices"])
            self.show_page("devices")
        default_name = split_device_name(eng.default_name or "—")[0]
        for value, glyph, title, desc in (
                ("auto", G["auto"], t("dev_auto_title"),
                 t("dev_auto_desc", name=default_name)),
                ("manual", G["pick"], t("dev_manual_title"), t("dev_manual_desc"))):
            _c, r, _d = self.card(glyph, title, desc)
            ttk.Radiobutton(r, value=value, variable=mode, style="Card.TRadiobutton",
                            command=set_mode).pack(side="right")

        manual = s["device_mode"] == "manual"
        self.heading(t("dev_list"))
        chosen = list(s["devices"])
        names = dict(s.get("device_names") or {})
        connected = {i for i, _n, _f in eng.devices}

        def toggle(dev_id, name, on):
            ids = [i for i in s["devices"] if i != dev_id]
            if on:
                ids.append(dev_id)
                s["device_names"][dev_id] = name
            app.set_devices("manual", ids)

        rows = [(i, n, f, True) for i, n, f in eng.devices]
        rows += [(i, names.get(i, "?"), 0, False) for i in chosen if i not in connected]
        for dev_id, name, ff, online in rows:
            main, adapter = split_device_name(name)
            tags = [adapter] if adapter else []
            if dev_id == eng.default_id:
                tags.append(t("dev_default_tag"))
            if not online:
                tags.append(t("dev_off_tag"))
            if dev_id in eng.device_ids:
                tags.append(t("dev_active_tag"))
            _c, r, _d = self.card(G[FORM_ICON.get(ff, "speakers")], short(main, 60),
                                  " · ".join(tags))
            if manual:
                self.switch(r, dev_id in chosen, lambda v, i=dev_id, n=name: toggle(i, n, v))
        if manual and not any(i in connected for i in chosen):
            self.text(self.body, t("dev_none_warn"), "warn").pack(
                fill="x", padx=(px(12), px(12)), pady=(px(6), 0))
        self.text(self.body, t("dev_hint"), "sub").pack(
            fill="x", padx=(px(12), px(12)), pady=(px(10), 0))

    # ---------------------------------------------------------------- apps
    def _page_apps(self):
        app, px, pal = self.app, self.app.px, self.app.pal
        self.text(self.body, t("apps_desc"), "sub").pack(
            fill="x", padx=(px(12), px(12)), pady=(0, px(10)))
        bar = tk.Frame(self.body, bg=pal["bg"])
        bar.pack(fill="x", padx=(px(12), px(12)), pady=(0, px(8)))
        self.search = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.search, width=28)
        ent.pack(side="left")
        ph = self.text(bar, t("apps_search"), "sub", wrap=False)

        def placeholder(*_a):
            if self.search.get() or ent.focus_get() is ent:
                ph.place_forget()
            else:
                ph.place(in_=ent, x=px(10), rely=0.5, anchor="w")
                ph.lift()
        ph.bind("<Button-1>", lambda e: ent.focus_set())
        ent.bind("<FocusIn>", placeholder)
        ent.bind("<FocusOut>", placeholder)
        self.search.trace_add("write", lambda *a: (placeholder(), self._fill_apps()))
        self.win.after(50, placeholder)
        forget = tk.Label(bar, text=t("apps_forget"), bg=pal["bg"], fg=pal["accent"],
                          cursor="hand2", font=(app.F["small"][0], app.F["small"][1],
                                                "underline"), bd=0)
        forget.pack(side="right")
        forget.bind("<Button-1>", lambda e: (app.forget_idle_apps(), self._fill_apps()))
        ttk.Button(bar, text=t("apps_add"), command=self._add_app).pack(
            side="left", padx=(px(8), 0))
        self.apps_box = tk.Frame(self.body, bg=pal["bg"])
        self.apps_box.pack(fill="x")
        self._fill_apps()

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
        self._fill_apps()

    def _app_rows(self):
        app, eng, s = self.app, self.app.engine, self.app.settings
        cur = {a[0].lower(): a for a in eng.apps}
        rows = {}
        for low, info in s["seen_apps"].items():
            rows[low] = dict(exe=info.get("exe", low), path=info.get("path", ""),
                             playing=False, present=False)
        for low in s["app_rules"]:
            rows.setdefault(low, dict(exe=low, path="", playing=False, present=False))
        for name, path, playing in eng.other_apps:
            low = name.lower()
            rows[low] = dict(exe=name, path=path or rows.get(low, {}).get("path", ""),
                             playing=playing, present=True)
        for low, a in cur.items():
            old = rows.get(low, {})
            rows[low] = dict(exe=a[0], path=a[4] or old.get("path", ""),
                             playing=a[3] or old.get("playing", False), present=True)
        out = []
        for low, r in rows.items():
            if r["exe"] == "System":
                title = t("app_system")
            else:
                title = (file_description(r["path"]) or FRIENDLY.get(low)
                         or app.app_label(r["exe"]))
            excluded = (not s["app_rules"][low]) if low in s["app_rules"] else \
                (s["call_protect"] and low in CALL_APPS)
            out.append(dict(r, low=low, title=title, excluded=excluded,
                            call=low in CALL_APPS))
        out.sort(key=lambda r: (not r["playing"], not r["present"], r["title"].lower()))
        return out

    def _fill_apps(self):
        if not hasattr(self, "apps_box") or not self.apps_box.winfo_exists():
            return
        app, px = self.app, self.app.px
        for w in self.apps_box.winfo_children():
            w.destroy()
        self._photos = []
        q = self.search.get().strip().lower()
        rows = [r for r in self._app_rows()
                if not q or q in r["title"].lower() or q in r["exe"].lower()]
        if not rows:
            self.text(self.apps_box, t("apps_empty"), "sub").pack(
                fill="x", padx=(px(12), px(12)), pady=px(8))
        size = px(24)
        for r in rows:
            img = None
            pil = exe_icon(r["path"], size) if r["path"] else None
            if pil is not None:
                img = tk.PhotoImage(data=png_b64(pil))
                self._photos.append(img)
            if r["playing"]:
                status = t("app_playing")
            elif r["present"]:
                status = t("app_open")
            else:
                status = t("app_idle")
            exe = r["exe"] if r["exe"] != "System" else ""
            desc = " · ".join(x for x in (exe, t("app_call") if r["call"] else "", status) if x)
            glyph = G["system"] if r["exe"] == "System" else G["app"]
            _c, right, _d = self.card(glyph, short(r["title"], 48), desc,
                                      parent=self.apps_box, image=img)
            self.switch(right, not r["excluded"],
                        lambda v, n=r["exe"]: app.set_app_rule(n, v))

    # ---------------------------------------------------------------- calls
    def _page_calls(self):
        app, s, px = self.app, self.app.settings, self.app.px
        _c, r, _d = self.card(G["calls"], t("calls_protect"), t("calls_desc"))
        self.switch(r, s["call_protect"], app.set_call_protect)
        self.card(G["apps"], t("calls_list_title"), ", ".join(CALL_NAMES))
        self.card(G["info"], t("calls_browser_title"), t("calls_browser"))
        self.text(self.body, t("calls_custom"), "sub").pack(
            fill="x", padx=(px(12), px(12)), pady=(px(10), 0))

    # ---------------------------------------------------------------- about
    def _page_about(self):
        app, px, pal = self.app, self.app.px, self.app.pal
        head = tk.Frame(self.body, bg=pal["bg"])
        head.pack(fill="x", padx=(px(12), px(12)), pady=(0, px(14)))
        self.big_logo = tk.PhotoImage(data=png_b64(app_icon(px(56))))
        tk.Label(head, image=self.big_logo, bg=pal["bg"], bd=0).pack(side="left")
        col = tk.Frame(head, bg=pal["bg"])
        col.pack(side="left", padx=(px(14), 0))
        tk.Label(col, text=APP_NAME, bg=pal["bg"], fg=pal["fg"], font=app.F["big"],
                 bd=0).pack(anchor="w")
        self.text(col, t("about_version", v=__version__ + (" beta" if BETA else "")),
                  "sub", wrap=False).pack(anchor="w")
        self.text(col, t("about_license"), "sub", wrap=False).pack(anchor="w")

        if app.update:
            _c, r, _d = self.card(G["update"], t("about_update", v=app.update[0]),
                                  t("about_update_desc"))
            ttk.Button(r, text=t("about_download"), style="Accent.TButton",
                       command=lambda: winsys.open_url(app.update[1])).pack(side="right")
        else:
            self.card(G["update"], t("about_latest"), t("about_latest_desc"))
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
