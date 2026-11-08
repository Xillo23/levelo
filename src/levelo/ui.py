"""Windows 11 style flyout panel + notification-area (tray) icon."""

import ctypes
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import pystray
import sv_ttk

from . import APP_NAME, APP_ID, BETA, __version__
from . import DONATE_URL as DEFAULT_DONATE_URL
from . import REPO_URL as DEFAULT_WEBSITE
from . import i18n, winsys
from .dsp import DEFAULT_PRESET, PRESETS
from .engine import CALL_APPS, Engine
from .settings_ui import (SettingsWindow, prewarm_apps, remember_apps,
                          split_device_name)
from .i18n import t
from .icons import app_icon, make_icon, png_b64
from .online import OnlineChecker, is_newer
from .storage import SETTINGS_FILE, load_json, save_json

PRESET_ORDER = ("light", "normal", "night")

# Support prompt: first after 5 h of balanced audio, then after 20 h and 50 h
# more, at most 3 times, never again after a click on "donate".
ASK_FIRST_HOURS = 5.0
ASK_LATER_HOURS = (20.0, 50.0)
ASK_MAX = 3
DONOR_PAUSE_S = 365 * 86400   # after "donate"/"I already donated": quiet for 1 year
OLD_PRESET_NAMES = {"Leggero": "light", "Normale": "normal", "Notte": "night"}

# Segoe Fluent Icons / MDL2 glyphs
ICON_SETTINGS, ICON_BACK, ICON_SPEAKER, ICON_CHEVRON = "", "", "", ""


def mix(c1, c2, k):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(x * k + y * (1 - k)):02x}" for x, y in zip(a, b))


def preset_label(key):
    return t(f"preset_{key}")


class App:
    def __init__(self, background, instance):
        self.instance = instance
        winsys.enable_dpi_awareness()
        winsys.set_app_user_model_id()

        self.settings = load_json(SETTINGS_FILE, {
            "enabled": True, "preset": DEFAULT_PRESET, "language": None,
            "welcomed": False,
            # support prompt: hours of balanced audio, peaks, when to ask next
            "stats_hours": 0.0, "stats_peaks": 0,
            "ask_next_hours": ASK_FIRST_HOURS, "ask_count": 0, "ask_toasted": 0,
            "donor_until": 0.0,    # unix time: no asks until then
            "links": {},           # cached links.json from the repository
            "update_notified": "",
            "device_mode": "auto",  # auto = Windows default device, manual = "devices"
            "devices": [],          # output device ids chosen in the settings
            "device_names": {},     # their names, shown while they are disconnected
            "call_protect": True,   # never touch voice-call apps
            "app_rules": {},        # app name (lowercase) -> balance it? (only exceptions)
            "seen_apps": {},        # apps seen recently, listed in the settings
            "headroom_tip": False})  # "Windows volume at maximum" told once
        s = self.settings
        s["preset"] = OLD_PRESET_NAMES.get(s["preset"], s["preset"])
        if s.pop("donated", False) and not s["donor_until"]:
            s["donor_until"] = time.time() + DONOR_PAUSE_S
        if s["preset"] not in PRESETS:
            s["preset"] = DEFAULT_PRESET
        lang = s.get("language") or winsys.installer_language() or i18n.system_language()
        i18n.set_language(lang)

        self._migrate_settings()
        self.settings_win = None
        self.engine = Engine(s["enabled"], s["preset"],
                             s["devices"] if s["device_mode"] == "manual" else None,
                             s["call_protect"], s["app_rules"])
        self.engine.start()
        self._flushed_s = 0.0
        self._flushed_peaks = 0
        self._last_flush = time.monotonic()
        self._last_seen = 0.0
        self._last_theme = time.monotonic()
        self._thanks_until = 0.0
        self.update = None     # (version, url) when a newer release exists

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.ui_q = queue.Queue()
        self.shown = False
        self.hidden_at = 0.0
        self._setup_theme()
        self.top = None
        self._build_panel()
        self._build_tray()
        OnlineChecker(lambda links, rel: self.post(
            lambda: self._apply_online(links, rel))).start()

        threading.Thread(target=instance.wait_loop,
                         args=(lambda: self.post(self.show),
                               lambda: self.post(self.quit)),
                         daemon=True).start()
        self.root.protocol("WM_SAVE_YOURSELF", lambda: self.engine.shutdown(2.0))
        self.root.after(50, self._loop)

        # read app names/icons of the audio history in the background, so the
        # Apps page opens instantly later
        self.root.after(8000, lambda: threading.Thread(
            target=prewarm_apps, daemon=True, name="Prewarm").start())
        if not s.get("welcomed"):
            s["welcomed"] = True
            self._save()
            self.root.after(1500, self._welcome)
        if not background:
            self.root.after(400, self.show)

    def _migrate_settings(self):
        """Settings of older versions (or damaged values) -> current format."""
        s = self.settings
        for key, typ, default in (("app_rules", dict, {}), ("seen_apps", dict, {}),
                                  ("device_names", dict, {}), ("devices", list, [])):
            if not isinstance(s.get(key), typ):
                s[key] = default
        old = s.pop("device", None)                 # 0.9.1: a single chosen device
        old_name = s.pop("device_name", "")
        if old and isinstance(old, str):
            s["device_mode"], s["devices"] = "manual", [old]
            s["device_names"][old] = old_name or "?"
        if s.get("device_mode") not in ("auto", "manual"):
            s["device_mode"] = "auto"
        s["call_protect"] = bool(s.get("call_protect", True))
        if s["stats_hours"] > 0:
            s["headroom_tip"] = True    # older versions already showed it in the panel
        self._save()

    def run(self):
        self.root.mainloop()
        self.engine.shutdown()     # volumes are back before the process ends

    def post(self, fn):
        self.ui_q.put(fn)

    # ------------------------------------------------------------------ theme
    def _setup_theme(self):
        self.dark = winsys.apps_dark()
        self._theme = (self.dark, winsys.taskbar_light())   # theme being shown
        sv_ttk.set_theme("dark" if self.dark else "light")
        fam = set(tkfont.families(self.root))
        self.icon_font = "Segoe Fluent Icons" if "Segoe Fluent Icons" in fam else "Segoe MDL2 Assets"
        base = "Segoe UI Variable Text" if "Segoe UI Variable Text" in fam else "Segoe UI"
        disp = "Segoe UI Variable Display" if "Segoe UI Variable Display" in fam else "Segoe UI"
        self.F = {"title": (disp, 12, "bold"), "big": (disp, 15, "bold"),
                  "body": (base, 10), "small": (base, 9)}
        self.scale = self.root.winfo_fpixels("1i") / 96.0
        if self.dark:
            self.pal = dict(bg="#1c1c1c", card="#2b2b2b", border="#404040", fg="#ffffff",
                            sub="#a8a8a8", hover="#2d2d2d", accent="#60cdff", boost="#6ccb5f", cut="#fc9d4f", grid="#3a3a3a")
        else:
            self.pal = dict(bg="#fafafa", card="#ffffff", border="#d6d6d6", fg="#1b1b1b",
                            sub="#616161", hover="#ededed", accent="#005fb8", boost="#0f7b0f", cut="#c75000", grid="#ebebeb")
        st = ttk.Style()
        st.configure("Panel.TFrame", background=self.pal["bg"])
        st.configure("ALCard.TFrame", background=self.pal["card"])
        for name in ("Card.TRadiobutton", "Card.Switch.TCheckbutton"):
            st.configure(name, background=self.pal["card"])
            st.map(name, background=[("active", self.pal["card"])])
        self.icon_photo = tk.PhotoImage(data=png_b64(app_icon(64)))
        self.root.iconphoto(True, self.icon_photo)
        self.fill_up = mix(self.pal["boost"], self.pal["card"], 0.30)
        self.fill_dn = mix(self.pal["cut"], self.pal["card"], 0.30)

    def px(self, v):
        return int(round(v * self.scale))

    def _label(self, parent, text, kind, **kw):
        bg, fg, font = {
            "body": ("bg", "fg", "body"), "sub": ("bg", "sub", "small"),
            "title": ("bg", "fg", "title"), "cardbig": ("card", "fg", "big"),
            "cardsub": ("card", "sub", "small"), "warn": ("bg", "cut", "small"),
        }[kind]
        return tk.Label(parent, text=text, bg=self.pal[bg], fg=self.pal[fg],
                        font=self.F[font], bd=0, **kw)

    # ------------------------------------------------------------------ panel
    def _build_panel(self):
        px, pal = self.px, self.pal
        was_shown = self.shown
        if self.top is not None:
            self.top.destroy()
            self.shown = False
        self.W = W = px(372)
        top = self.top = tk.Toplevel(self.root)
        top.withdraw()
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=pal["border"])

        outer = ttk.Frame(top, style="Panel.TFrame", padding=(px(18), px(16), px(18), px(14)))
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        wrap = W - px(40)

        head = ttk.Frame(outer, style="Panel.TFrame")
        head.pack(fill="x")
        self.logo = tk.PhotoImage(data=png_b64(app_icon(px(34))))
        tk.Label(head, image=self.logo, bg=pal["bg"], bd=0).pack(side="left")
        tt = ttk.Frame(head, style="Panel.TFrame")
        tt.pack(side="left", padx=(px(10), 0))
        self._label(tt, APP_NAME, "title").pack(anchor="w")
        self.status_sub = self._label(tt, "", "sub")
        self.status_sub.pack(anchor="w")
        self.enabled_var = tk.BooleanVar(value=self.settings["enabled"])
        ttk.Checkbutton(head, style="Switch.TCheckbutton", variable=self.enabled_var,
                        command=lambda: self.set_enabled(self.enabled_var.get())
                        ).pack(side="right")
        self._icon_button(head, ICON_SETTINGS, t("settings"),
                          lambda: self.open_settings("general")).pack(side="right",
                                                                   padx=(0, px(6)))
        self.head = head

        main = self.main = ttk.Frame(outer, style="Panel.TFrame")
        main.pack(fill="x")
        self.update_lbl = tk.Label(main, text="", bg=pal["bg"], fg=pal["accent"],
                                   font=(self.F["small"][0], self.F["small"][1], "underline"),
                                   cursor="hand2", bd=0, anchor="w")
        self.update_lbl.bind("<Button-1>", lambda e: self.update and winsys.open_url(self.update[1]))
        self.update_shown = False

        card = tk.Frame(main, bg=pal["card"], highlightthickness=1,
                        highlightbackground=pal["border"], bd=0)
        card.pack(fill="x", pady=(px(16), 0))
        self.status_card = card
        cin = ttk.Frame(card, style="ALCard.TFrame", padding=(px(14), px(12), px(14), px(10)))
        cin.pack(fill="x")
        self.state_big = self._label(cin, "", "cardbig")
        self.state_big.pack(anchor="w")
        self.state_sub = self._label(cin, "", "cardsub", wraplength=wrap - px(30),
                                     justify="left")
        self.state_sub.pack(anchor="w", pady=(px(1), px(8)))
        self.GW, self.GH = W - px(66), px(78)
        self.graph = tk.Canvas(cin, width=self.GW, height=self.GH, bg=pal["card"],
                               highlightthickness=0, bd=0)
        self.graph.pack(fill="x")
        leg = ttk.Frame(cin, style="ALCard.TFrame")
        leg.pack(fill="x", pady=(px(4), 0))
        self._label(leg, t("graph_window"), "cardsub").pack(side="left")
        self._label(leg, t("graph_legend"), "cardsub").pack(side="right")
        # which output is being balanced (click: settings)
        devrow = tk.Frame(cin, bg=pal["card"], cursor="hand2")
        devrow.pack(fill="x", pady=(px(8), 0))
        tk.Label(devrow, text=ICON_SPEAKER, font=(self.icon_font, 10), bg=pal["card"],
                 fg=pal["sub"], bd=0).pack(side="left")
        self.dev_lbl = self._label(devrow, "", "cardsub", anchor="w")
        self.dev_lbl.pack(side="left", fill="x", expand=True, padx=(px(6), 0))
        tk.Label(devrow, text=ICON_CHEVRON, font=(self.icon_font, 8), bg=pal["card"],
                 fg=pal["sub"], bd=0).pack(side="right")
        for w in (devrow, *devrow.winfo_children()):
            w.bind("<Button-1>", lambda e: self.open_settings("devices"))

        self._label(main, t("intensity"), "body").pack(anchor="w", pady=(px(16), px(6)))
        seg = ttk.Frame(main, style="Panel.TFrame")
        seg.pack(fill="x")
        self.preset_var = tk.StringVar(value=self.settings["preset"])
        for i, key in enumerate(PRESET_ORDER):
            ttk.Radiobutton(seg, text=preset_label(key), value=key, variable=self.preset_var,
                            style="Toggle.TButton",
                            command=lambda: self.set_preset(self.preset_var.get())
                            ).grid(row=0, column=i, sticky="ew",
                                   padx=(0 if i == 0 else px(4), 0))
            seg.columnconfigure(i, weight=1, uniform="seg")
        self.preset_desc = self._label(main, "", "sub", wraplength=wrap, justify="left")
        self.preset_desc.pack(anchor="w", pady=(px(6), 0))
        self.warn = self._label(main, "", "warn", wraplength=wrap, justify="left")
        self.warn.pack(anchor="w")

        # support card (shown only when it is time to ask)
        self.ask_card = tk.Frame(main, bg=pal["card"], highlightthickness=1,
                                 highlightbackground=pal["border"], bd=0)
        ain = ttk.Frame(self.ask_card, style="ALCard.TFrame",
                        padding=(px(14), px(10), px(14), px(12)))
        ain.pack(fill="x")
        self.ask_title = self._label(ain, t("ask_title"), "cardbig")
        self.ask_title.config(font=self.F["title"])
        self.ask_title.pack(anchor="w")
        self.ask_body = self._label(ain, "", "cardsub", wraplength=wrap - px(30),
                                    justify="left")
        self.ask_body.pack(anchor="w", pady=(px(2), px(8)))
        self.ask_btns = ttk.Frame(ain, style="ALCard.TFrame")   # buttons + link
        self.ask_btns.pack(fill="x")
        row = ttk.Frame(self.ask_btns, style="ALCard.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text=t("ask_yes"), style="Accent.TButton",
                   command=self.donate).pack(side="left")
        ttk.Button(row, text=t("ask_later"),
                   command=self.ask_later).pack(side="left", padx=(px(6), 0))
        done = self._label(self.ask_btns, t("ask_done"), "cardsub", cursor="hand2")
        done.config(font=(self.F["small"][0], self.F["small"][1], "underline"))
        done.pack(anchor="w", pady=(px(8), 0))
        done.bind("<Button-1>", lambda e: self.already_donated())
        self.ask_shown = False

        self.sep = ttk.Separator(main)
        self.sep.pack(fill="x", pady=(px(10), px(10)))
        foot = ttk.Frame(main, style="Panel.TFrame")
        foot.pack(fill="x")
        self.auto_var = tk.BooleanVar(value=winsys.autostart_enabled())
        ttk.Checkbutton(foot, text=t("autostart"), variable=self.auto_var,
                        command=lambda: self.set_autostart(self.auto_var.get())
                        ).pack(side="left")
        ttk.Button(foot, text=t("quit"), command=self.quit).pack(side="right")
        self.donate_btn = None
        if self.donate_url and not self._is_donor():
            self.donate_btn = ttk.Button(foot, text=t("donate_short"), command=self.donate)
            self.donate_btn.pack(side="right", padx=(0, px(6)))
        bottom = ttk.Frame(main, style="Panel.TFrame")
        bottom.pack(fill="x", pady=(px(10), 0))
        self._label(bottom, t("footer"), "sub", justify="left").pack(side="left")
        self._label(bottom, f"v{__version__}" + (" beta" if BETA else ""), "sub"
                    ).pack(side="right", anchor="s")

        top.bind("<FocusOut>", self._on_focus_out)
        top.bind("<Escape>", lambda e: self.hide())
        if was_shown:
            self.show()

    # --------------------------------------------------------------- settings
    def _icon_button(self, parent, glyph, tip, command, size=11):
        """Flat Fluent-icon button (like the gear in Windows flyouts)."""
        pal = self.pal
        b = tk.Label(parent, text=glyph, font=(self.icon_font, size), bg=pal["bg"],
                     fg=pal["fg"], bd=0, cursor="hand2", padx=self.px(7), pady=self.px(5))
        b.bind("<Enter>", lambda e: b.config(bg=pal["hover"]))
        b.bind("<Leave>", lambda e: b.config(bg=pal["bg"]))
        b.bind("<Button-1>", lambda e: command())
        b.tip = tip
        return b

    def open_settings(self, page="general"):
        self.hide()
        win = self.settings_win
        if win is not None and win.alive():
            if page != win.page:
                win.show_page(page)
            win.focus()
            if win._update:
                win._update()
        else:
            self.settings_win = SettingsWindow(self, page)

    @staticmethod
    def _short(name, n=44):
        return name if len(name) <= n else name[:n - 1] + "…"

    def app_label(self, name):
        if name == "System":
            return t("app_system")
        base = name[:-4] if name.lower().endswith(".exe") else name
        return base[:1].upper() + base[1:]

    def set_devices(self, mode, ids):
        s = self.settings
        s["device_mode"] = "manual" if mode == "manual" else "auto"
        s["devices"] = list(dict.fromkeys(ids))
        names = dict(self.engine_device_names())
        for i in s["devices"]:
            if i in names:
                s["device_names"][i] = names[i]
        s["device_names"] = {i: n for i, n in s["device_names"].items() if i in s["devices"]}
        self._save()
        self.engine.set_devices(s["devices"] if s["device_mode"] == "manual" else None)

    def engine_device_names(self):
        return [(i, n) for i, n, _f in self.engine.devices]

    def set_call_protect(self, on):
        s = self.settings
        s["call_protect"] = bool(on)
        # the switch wins over earlier per-app choices for call apps
        s["app_rules"] = {k: v for k, v in s["app_rules"].items() if k not in CALL_APPS}
        self._save()
        self.engine.set_app_rules(on, s["app_rules"])

    def set_app_rule(self, name, balance):
        s = self.settings
        low = name.lower()
        default = not (s["call_protect"] and low in CALL_APPS)
        if bool(balance) == default:
            s["app_rules"].pop(low, None)      # back to the default: no rule needed
        else:
            s["app_rules"][low] = bool(balance)
        self._save()
        self.engine.set_app_rules(s["call_protect"], s["app_rules"])

    def report_bug(self):
        site = self.website or ""
        if "github.com/" in site:
            site = site.rstrip("/") + "/issues/new/choose"
        winsys.open_url(site)

    def show(self):
        top, px = self.top, self.px
        if self.shown:
            top.lift()
            top.focus_force()
            return
        self.refresh()
        top.update_idletasks()
        h = top.winfo_reqheight()
        self._anchor = winsys.panel_anchor()
        x, y = self._panel_xy(h)
        off = px(18) if self._anchor[1] != "top" else -px(18)
        top.geometry(f"{self.W}x{h}+{x}+{y + off}")
        top.attributes("-alpha", 0.0)
        top.deiconify()
        top.lift()
        top.focus_force()
        try:
            hwnd = ctypes.windll.user32.GetParent(top.winfo_id()) or top.winfo_id()
            winsys.dwm_style(hwnd, self.dark)
        except Exception:
            pass
        self.shown = True
        steps = 9
        for i in range(1, steps + 1):  # slide up + fade in, like Windows flyouts
            k = 1 - (1 - i / steps) ** 3
            top.after(i * 15, lambda k=k: (
                top.geometry(f"+{x}+{int(y + off * (1 - k))}"),
                top.attributes("-alpha", k)))

    def hide(self):
        if self.shown:
            self.top.withdraw()
            self.shown = False
            self.hidden_at = time.monotonic()

    def toggle(self):
        if self.shown:
            self.hide()
        elif time.monotonic() - self.hidden_at > 0.35:
            self.show()

    def _on_focus_out(self, _e):
        def check():
            if self.shown and self.top.focus_get() is None:
                self.hide()
        self.top.after(150, check)

    # ---------------------------------------------------------------- actions
    def _save(self):
        save_json(SETTINGS_FILE, self.settings)

    def set_enabled(self, on):
        self.settings["enabled"] = on
        self.enabled_var.set(on)
        self.engine.set_enabled(on)
        self._save()
        self._update_tray()

    def set_preset(self, key):
        self.settings["preset"] = key
        self.preset_var.set(key)
        self.engine.set_preset(key)
        self._save()
        self._update_tray()

    def set_autostart(self, on):
        winsys.set_autostart(on)
        self.auto_var.set(winsys.autostart_enabled())
        self._update_tray()

    def set_language(self, code):
        self.settings["language"] = code
        i18n.set_language(code)
        self._save()
        self._build_panel()
        self._update_tray()
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.build()

    # ---------------------------------------------------------------- support
    def _flush_stats(self):
        s, eng = self.settings, self.engine
        s["stats_hours"] += (eng.active_s - self._flushed_s) / 3600.0
        s["stats_peaks"] += eng.peaks - self._flushed_peaks
        self._flushed_s, self._flushed_peaks = eng.active_s, eng.peaks
        self._save()
        if self._should_ask() and s["ask_toasted"] <= s["ask_count"]:
            s["ask_toasted"] = s["ask_count"] + 1
            self._save()
            try:
                self.tray.notify(t("ask_toast", hours=self._hours()), APP_NAME)
            except Exception:
                pass

    def _hours(self):
        return f"{self.settings['stats_hours']:.0f}"

    def _is_donor(self):
        return time.time() < float(self.settings.get("donor_until", 0.0))

    def _should_ask(self):
        s = self.settings
        return (bool(self.donate_url) and not self._is_donor() and s["ask_count"] < ASK_MAX
                and s["stats_hours"] >= s["ask_next_hours"])

    def donate(self):
        winsys.open_url(self.donate_url)
        self.already_donated()

    def already_donated(self):
        """Honor system: after a donation (or "I already donated") stay quiet for a
        year and hide the panel button; the tray menu item stays available.
        The "Not now" counter restarts, so after a year it can ask again."""
        self.settings["donor_until"] = time.time() + DONOR_PAUSE_S
        self.settings["ask_count"] = 0
        self.settings["ask_next_hours"] = self.settings["stats_hours"]
        self.settings["ask_toasted"] = 0
        self._save()
        self._thanks_until = time.monotonic() + 6.0
        if self.donate_btn is not None:
            self.donate_btn.pack_forget()
            self.donate_btn = None
        self.refresh()
        self._resize()

    def ask_later(self):
        s = self.settings
        s["ask_count"] += 1
        if s["ask_count"] <= len(ASK_LATER_HOURS):
            s["ask_next_hours"] = s["stats_hours"] + ASK_LATER_HOURS[s["ask_count"] - 1]
        self._save()
        self.refresh()

    def _update_ask_card(self):
        thanks = time.monotonic() < self._thanks_until
        show = thanks or self._should_ask()
        if show:
            if thanks:
                self.ask_title.config(text=t("thanks"))
                if self.ask_btns.winfo_manager():
                    self.ask_body.pack_forget()
                    self.ask_btns.pack_forget()
                    self._resize()
            else:
                self.ask_title.config(text=t("ask_title"))
                self.ask_body.config(text=t("ask_body", hours=self._hours(),
                                            peaks=self.settings["stats_peaks"]))
                if not self.ask_btns.winfo_manager():
                    self.ask_body.pack(anchor="w", pady=(self.px(2), self.px(8)))
                    self.ask_btns.pack(fill="x")
                    self._resize()
            if not self.ask_shown:
                self.ask_card.pack(fill="x", pady=(self.px(12), 0), before=self.sep)
                self.ask_shown = True
                self._resize()
        elif self.ask_shown:
            self.ask_card.pack_forget()
            self.ask_shown = False
            self._resize()

    def _resize(self):
        """Keep the panel anchored above the taskbar when its height changes."""
        if not self.shown:
            return
        self.top.update_idletasks()
        h = self.top.winfo_reqheight()
        x, y = self._panel_xy(h)
        self.top.geometry(f"{self.W}x{h}+{x}+{y}")

    def _panel_xy(self, h):
        """Next to the taskbar, on the monitor where the tray icon was clicked."""
        (l, t_, r, b), edge = getattr(self, "_anchor", None) or winsys.panel_anchor()
        m = self.px(12)
        x = l + m if edge == "left" else r - self.W - m
        y = t_ + m if edge == "top" else b - h - m
        return x, max(t_ + m, y)

    def quit(self):
        self._flush_stats()
        self.hide()
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.destroy()
        try:
            self.tray.stop()
        except Exception:
            pass
        self.engine.shutdown()
        self.root.after(10, self.root.destroy)

    def _welcome(self):
        try:
            self.tray.notify(t("welcome"), APP_NAME)
        except Exception:
            pass

    def _follow_theme(self):
        """Windows light/dark mode changed: repaint everything, like native apps."""
        theme = (winsys.apps_dark(), winsys.taskbar_light())
        if theme == self._theme:
            return
        self._setup_theme()
        self._build_panel()
        fg = (0, 0, 0, 255) if theme[1] else (255, 255, 255, 255)
        self.ico_on = make_icon(64, fg=fg)
        self.ico_off = make_icon(64, fg=fg, dim=True)
        self._update_tray()
        if self.settings_win is not None and self.settings_win.alive():
            self.settings_win.build()

    # ------------------------------------------------------------------- tray
    def _build_tray(self):
        fg = (0, 0, 0, 255) if winsys.taskbar_light() else (255, 255, 255, 255)
        self.ico_on = make_icon(64, fg=fg)
        self.ico_off = make_icon(64, fg=fg, dim=True)
        s, M, I = self.settings, pystray.Menu, pystray.MenuItem

        def act(fn):
            return lambda icon, item: self.post(fn)

        def preset_item(key):
            return I(lambda it: preset_label(key), act(lambda: self.set_preset(key)),
                     checked=lambda it: s["preset"] == key, radio=True)

        def lang_item(code, name):
            return I(name, act(lambda: self.set_language(code)),
                     checked=lambda it: i18n.current() == code, radio=True)

        items = [
            I(lambda it: t("menu_open"), act(self.toggle), default=True),
            I(lambda it: t("menu_enabled"),
              act(lambda: self.set_enabled(not s["enabled"])),
              checked=lambda it: s["enabled"]),
            I(lambda it: t("menu_intensity"), M(*[preset_item(k) for k in PRESET_ORDER])),
            I(lambda it: t("settings"), act(lambda: self.open_settings("general"))),
            I(lambda it: t("menu_language"),
              M(*[lang_item(c, n) for c, n in i18n.LANGUAGES.items()])),
            M.SEPARATOR,
            I(lambda it: t("autostart"),
              act(lambda: self.set_autostart(not winsys.autostart_enabled())),
              checked=lambda it: winsys.autostart_enabled()),
        ]
        items += [
            I(lambda it: t("menu_website"), act(lambda: winsys.open_url(self.website)),
              visible=lambda it: bool(self.website)),
            I(lambda it: t("menu_donate"), act(lambda: winsys.open_url(self.donate_url)),
              visible=lambda it: bool(self.donate_url)),
            I(lambda it: t("menu_update", v=self.update[0] if self.update else ""),
              act(lambda: self.update and winsys.open_url(self.update[1])),
              visible=lambda it: bool(self.update)),
        ]
        items += [M.SEPARATOR, I(lambda it: t("menu_quit"), act(self.quit))]
        self.tray = pystray.Icon(APP_ID, self.ico_on if s["enabled"] else self.ico_off,
                                 self._tray_title(), M(*items))
        self.tray.run_detached()

    # ----------------------------------------------------------------- online
    @property
    def donate_url(self):
        return self.settings["links"].get("donate_url") or DEFAULT_DONATE_URL

    @property
    def website(self):
        return self.settings["links"].get("website") or DEFAULT_WEBSITE

    def _apply_online(self, links, release):
        if links is not None and links != self.settings["links"]:
            had_donate = bool(self.donate_url)
            self.settings["links"] = links
            self._save()
            if bool(self.donate_url) != had_donate:
                self._build_panel()          # show/hide the donate button
        if release and is_newer(release[0]):
            self.update = release
            if self.settings["update_notified"] != release[0]:
                self.settings["update_notified"] = release[0]
                self._save()
                try:
                    self.tray.notify(t("update_toast", v=release[0]), APP_NAME)
                except Exception:
                    pass
        self._update_tray()
        if self.shown:
            self.refresh()

    def _update_update_bar(self):
        if self.update and not self.update_shown:
            self.update_lbl.config(text=t("update_bar", v=self.update[0]))
            self.update_lbl.pack(fill="x", pady=(self.px(10), 0), before=self.status_card)
            self.update_shown = True
            self._resize()

    def _tray_title(self):
        if not self.settings["enabled"]:
            return t("tray_off", app=APP_NAME)
        return t("tray_on", app=APP_NAME, preset=preset_label(self.settings["preset"]))

    def _update_tray(self):
        try:
            self.tray.icon = self.ico_on if self.settings["enabled"] else self.ico_off
            self.tray.title = self._tray_title()
            self.tray.update_menu()
        except Exception:
            pass

    # --------------------------------------------------------------- live view
    def _loop(self):
        try:
            while True:
                self.ui_q.get_nowait()()
        except queue.Empty:
            pass
        if time.monotonic() - self._last_seen > 3.0:
            self._last_seen = time.monotonic()
            if remember_apps(self.settings, self.engine.apps, self.engine.other_apps):
                self._save()
        if (not self.settings["headroom_tip"] and self.settings["enabled"]
                and self.engine.headroom_short):
            self.settings["headroom_tip"] = True     # tell it once, never nag
            self._save()
            try:
                self.tray.notify(t("warn_headroom"), APP_NAME)
            except Exception:
                pass
        if time.monotonic() - self._last_theme > 3.0:
            self._last_theme = time.monotonic()
            self._follow_theme()
        if time.monotonic() - self._last_flush > 30.0:
            self._last_flush = time.monotonic()
            self._flush_stats()
        if self.shown:
            self.refresh()
        self.root.after(50, self._loop)

    def refresh(self):
        eng, s = self.engine, self.settings
        g = eng.gain
        if eng.worker_error:
            self.status_sub.config(text=t("status_error"))
            big, sub = t("error_big"), t("error_sub")
        elif not s["enabled"]:
            self.status_sub.config(text=t("status_paused"))
            big, sub = t("paused_big"), t("paused_sub")
        else:
            self.status_sub.config(text=t("status_active", preset=preset_label(s["preset"])))
            st = eng.state
            if st == "silence":
                big, sub = t("listen_big"), t("listen_sub")
            elif st == "loud":
                big, sub = t("loud_big", db=f"{abs(g):.0f}"), t("loud_sub")
            elif st == "quiet":
                big, sub = t("quiet_big", db=f"{g:.0f}"), t("quiet_sub")
            else:
                big, sub = t("ok_big"), t("ok_sub")
            if eng.call_app:
                sub = t("call_sub", app=self._app_label(eng.call_app))
        self.state_big.config(text=big)
        self.state_sub.config(text=sub)
        self.preset_desc.config(text=t(f"desc_{s['preset']}"))
        devs = [split_device_name(n)[0] for n in eng.device.split(" + ") if n]
        self.dev_lbl.config(text=self._short(" + ".join(devs) or "—"))
        warn = ""
        if eng.device_missing:
            off = split_device_name(s["device_names"].get(eng.missing[0], "?"))[0]
            warn = t("warn_device", name=self._short(off, 30))
        if warn:
            self.warn.config(text=warn)
            self.warn.pack_configure(pady=(self.px(8), 0))
        else:
            self.warn.config(text="")
            self.warn.pack_configure(pady=0)
        self._update_ask_card()
        self._update_update_bar()
        self._draw_graph()

    def _draw_graph(self):
        gr, pal, px = self.graph, self.pal, self.px
        gr.delete("all")
        w = gr.winfo_width() if gr.winfo_width() > 10 else self.GW
        h = self.GH
        lo, hi = -30.0, 12.0

        def y_of(v):
            v = max(lo, min(hi, v))
            return (hi - v) / (hi - lo) * (h - 4) + 2

        y0 = y_of(0)
        for v in (-20, -10, 10):
            gr.create_line(0, y_of(v), w, y_of(v), fill=pal["grid"], dash=(2, 4))
        hist = list(self.engine.history)
        n = len(hist)
        xs = [i * (w - px(5)) / (n - 1) for i in range(n)]
        up = [0, y0] + [c for x, v in zip(xs, hist) for c in (x, y_of(max(v, 0)))] + [xs[-1], y0]
        dn = [0, y0] + [c for x, v in zip(xs, hist) for c in (x, y_of(min(v, 0)))] + [xs[-1], y0]
        gr.create_polygon(up, fill=self.fill_up, outline="")
        gr.create_polygon(dn, fill=self.fill_dn, outline="")
        gr.create_line(0, y0, w, y0, fill=pal["sub"])
        last = hist[-1]
        col = pal["boost"] if last >= 0 else pal["cut"]
        gr.create_line(*[c for x, v in zip(xs, hist) for c in (x, y_of(v))],
                       fill=col, width=max(1, px(2)))
        r = px(3.5)
        gr.create_oval(xs[-1] - r, y_of(last) - r, xs[-1] + r, y_of(last) + r,
                       fill=col, outline="")
