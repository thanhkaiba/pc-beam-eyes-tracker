"""Tkinter window (dark, sv-ttk + Lucide icons): choose webcam or phone, then a sidebar with Home, Phone, Settings, Help.

  Launch: webcam or phone → set your centre (webcam) or wait for the phone.
  Home: the view the game gets, Recenter / Pause / Test, the feel.  Phone: this PC for the phone.
  Settings: rows with the common control on the right, expert ones behind "›".  Help: diagnostics.
  Few words on screen; the detail is in tooltips.

Animations are light and time based (`anim.py`): a window fade-in, a breathing status dot, the
centre screen sliding into the tabs, the cockpit camera gliding to the sent pose over a scrolling
road, and the eye-calibration dot gliding between points. They run on a ~30 fps timer that only
redraws what is visible; a slow PC drops frames instead of slowing the motion.

The window never touches the engine thread directly: it reads `engine.state` on a 50 ms timer
and sends commands (calibrate, recenter, profile updates) that the engine executes itself.
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from dataclasses import replace
from tkinter import ttk
from typing import Dict, Optional

import math

from . import __version__
from . import anim
from . import protocol
from . import sim
from .app import App
from .autocentre import AutoCentreSettings
from .engine import CalibrationPhase, EngineState
from .faceloss import TrackingState
from .filters import FilterType
from .gaze import EyeAssistSettings, GazeSource
from .icons import Icons
from .hotkeys import HotkeySettings, parse_key
from .inputs.base import SourceStatus
from .mapping import AxisSettings, ResponseCurve
from .net.discovery import local_ipv4_addresses
from .outputs import games as game_table
from .outputs.mouse import MouseMode, MouseSettings
from .pose import Axis, HeadPose
from .profile import ApiSettings, FreetrackInterface, SourceKind, TrackingProfile
from .profiles import PRESETS, category_of
from .selfcheck import CheckResult

POLL_MS = 50
PREVIEW_MS = 66
ANIM_MS = 33          # ~30 fps for the light animations (glides, pulses, the scrolling road)
FADE_SECONDS = 0.25   # window fade-in at launch
SLIDE_SECONDS = 0.28  # centre screen → tabs
ROAD_PERIOD = 0.9     # one dash period of the cockpit road, same as the Android app
ACCENT = "#4cc2ff"
# dark theme: pages on PAGE_BG, grouped controls on CARD_BG cards (sv-ttk's own surface colour)
PAGE_BG = "#121212"
CARD_BG = "#1c1c1c"
SIDEBAR_BG = "#0d0d0d"
NAV_ON = "#232323"
NAV_HOVER = "#191919"
BORDER = "#2a2a2a"
TEXT = "#f2f2f2"
MUTED = "#8f949a"
OK = "#3ddc84"
WARN = "#f5b83d"
ERR = "#ff6b6b"
FONT = "Segoe UI"
COCKPIT_W, COCKPIT_H = 720, 320


def _apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        import sv_ttk
        sv_ttk.set_theme("dark", root)
    except Exception:   # no sv-ttk: a dark clam is still readable
        style.theme_use("clam")
        style.configure(".", background=CARD_BG, foreground=TEXT, fieldbackground="#2b2b2b")
        style.configure("Accent.TButton", background="#2f6fb3")
        style.map("Toggle.TButton", background=[("selected", "#2f6fb3")])
    root.configure(bg=PAGE_BG)
    style.configure("Page.TFrame", background=PAGE_BG)
    style.configure("Page.TLabel", background=PAGE_BG, foreground=TEXT)
    style.configure("PageMuted.TLabel", background=PAGE_BG, foreground=MUTED)
    style.configure("Title.TLabel", background=PAGE_BG, foreground=TEXT, font=(FONT, 22, "bold"))
    style.configure("Group.TLabel", background=PAGE_BG, foreground=MUTED, font=(FONT, 9, "bold"))
    style.configure("Big.TLabel", font=(FONT, 15, "bold"))
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Small.TLabel", foreground=MUTED, font=(FONT, 9))
    style.configure("Chip.TLabel", background=PAGE_BG, foreground=MUTED, font=(FONT, 10))
    style.configure("Page.TButton", background=PAGE_BG)
    style.configure("Page.Toolbutton", background=PAGE_BG)
    # sv-ttk maps the background by state, which beats `configure`: map the page styles too
    for name in ("Page.TFrame", "Page.TLabel", "PageMuted.TLabel", "Title.TLabel", "Group.TLabel", "Chip.TLabel",
                 "Page.TButton", "Page.Toolbutton"):
        style.map(name, background=[("!disabled", PAGE_BG), ("disabled", PAGE_BG)])


class Tooltip:
    """A small dark hint under a widget after a short hover: the detail the label leaves out."""

    def __init__(self, widget, text) -> None:
        self.widget, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None) -> None:
        self.job = self.widget.after(450, self._show)

    def _show(self) -> None:
        text = self.text() if callable(self.text) else self.text
        if not text or self.tip is not None:
            return
        x, y = self.widget.winfo_rootx() + 8, self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=text, bg="#2b2b2b", fg=TEXT, font=(FONT, 9), padx=8, pady=4,
                 wraplength=320, justify="left").pack()

    def _hide(self, _e=None) -> None:
        if self.job is not None:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


def _fmt(p: Optional[HeadPose]) -> str:
    if p is None:
        return "no face"
    return f"yaw {p.yaw:6.1f}°  pitch {p.pitch:6.1f}°  roll {p.roll:6.1f}°"


class HeadTrackWindow:
    def __init__(self, app: App, steam=None):
        self.app = app
        self.steam = steam
        self.root = tk.Tk()
        self.root.title(f"HeadTrack PC {__version__}")
        self.root.minsize(1000, 720)
        self.root.geometry("1060x760")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._preview_image = None
        self._cal_window = None
        self._last_api_requests = -2
        self._last_tuning_label = ""
        self._last_check_time = 0.0
        self._centre_done = False
        self._stage = "choose"   # choose → webcam (set your centre) or phone (waiting) → tabs
        self._phone_fresh_since: Optional[float] = None
        self._firewall_ok: Optional[bool] = None
        self._last_webcam_error: Optional[str] = None
        self._last_state: Optional[EngineState] = None
        self._axis_vars: Dict[str, Dict[str, tk.Variable]] = {}
        # animation state: everything time based so a busy PC just drops frames, never speeds up
        self._anim_last = time.monotonic()
        self._fade = anim.Transition(FADE_SECONDS)
        self._fade_armed = False   # alpha is 0 and the fade starts on the first animation tick
        self._slide = anim.Transition(SLIDE_SECONDS)
        self._tabs_shown = False
        self._cam = {k: anim.Eased(0.0, seconds=0.12, snap=1e-4) for k in ("pan_x", "pan_y", "roll", "parallax_x", "parallax_y")}
        self._cam["zoom"] = anim.Eased(1.0, seconds=0.12, snap=1e-4)
        self._gaze_dot = (anim.Eased(0.5, seconds=0.1), anim.Eased(0.5, seconds=0.1))
        self._gaze_seen = False
        self._centre_fill = anim.Eased(0.0, seconds=0.1)
        self._cal_dot: Optional[tuple] = None
        self._cal_last = 0.0
        self._cal_fade = anim.Transition(0.2)
        self._scene = None   # the road photo cockpit; None → the vector drawing
        self._scene_key: Optional[tuple] = None
        self._scene_photo = None
        try:
            from .scene import CockpitScene
            self._scene = CockpitScene(COCKPIT_W, COCKPIT_H)
        except Exception:
            pass   # photo not fetched (tools/fetch_assets.py) or Pillow missing
        self._build()
        self._start_fade()
        self.root.after(POLL_MS, self._poll)
        self.root.after(PREVIEW_MS, self._preview)
        self.root.after(ANIM_MS, self._animate)
        if self.steam is not None:
            self.root.after(100, self._steam_callbacks)

    def _steam_callbacks(self) -> None:
        self.steam.run_callbacks()
        self.root.after(100, self._steam_callbacks)

    # --- layout -------------------------------------------------------------------------------
    def _build(self) -> None:
        root = self.root
        _apply_theme(root)
        self.icons = Icons()
        self.status_var = tk.StringVar(value="Starting…")
        self._frame_bg = PAGE_BG
        self.container = ttk.Frame(root, style="Page.TFrame")
        self.container.pack(fill="both", expand=True)
        p = self.app.profile
        self.source_var = tk.StringVar(value=p.source.value)
        self.cam_label = tk.StringVar(value=self._camera_label(p.camera.index))
        self.cam_label.trace_add("write", lambda *_: self._apply_camera())
        self.choose_frame = self._build_choose(self.container)
        self.centre_frame = self._build_centre(self.container)
        self.phone_frame = self._build_phone_wait(self.container)
        # after the first screen: a sidebar on the left, one page at a time on the right
        self.main = ttk.Frame(self.container, style="Page.TFrame")
        self.page_var = tk.StringVar(value="home")
        side = tk.Frame(self.main, bg=SIDEBAR_BG, width=200)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=SIDEBAR_BG)
        brand.pack(fill="x", padx=18, pady=(20, 22))
        logo = self.icons.get("scan-face", 22, ACCENT)
        if logo is not None:
            tk.Label(brand, image=logo, bg=SIDEBAR_BG).pack(side="left", padx=(0, 8))
        tk.Label(brand, text="HeadTrack", bg=SIDEBAR_BG, fg=TEXT, font=(FONT, 13, "bold")).pack(side="left")
        body = ttk.Frame(self.main, style="Page.TFrame")
        body.pack(side="left", fill="both", expand=True)
        self.pages: Dict[str, ttk.Frame] = {
            "home": self._build_home(body), "phone": self._build_phone(body),
            "settings": self._build_settings(body), "help": self._build_help(body),
        }
        # Windows 11 style navigation: flat rows, the current one lit with an accent bar on its left
        self._nav: Dict[str, tuple] = {}
        for key, icon, label in (("home", "house", "Home"), ("phone", "smartphone", "Phone"),
                                 ("settings", "settings", "Settings"), ("help", "circle-help", "Help")):
            item = tk.Frame(side, bg=SIDEBAR_BG, cursor="hand2")
            item.pack(fill="x", padx=8, pady=1)
            bar_ = tk.Frame(item, bg=SIDEBAR_BG, width=3)
            bar_.pack(side="left", fill="y", pady=8)
            pic = tk.Label(item, image=self.icons.get(icon, 18) or "", bg=SIDEBAR_BG)
            pic.pack(side="left", padx=(12, 12), pady=9)
            txt = tk.Label(item, text=label, bg=SIDEBAR_BG, fg=TEXT, font=(FONT, 10), anchor="w")
            txt.pack(side="left", fill="x", expand=True)
            self._nav[key] = (item, bar_, pic, txt)
            for w in (item, pic, txt):
                w.bind("<Button-1>", lambda _e, k=key: self.show_page(k))
                w.bind("<Enter>", lambda _e, k=key: self._nav_paint(k, hover=True))
                w.bind("<Leave>", lambda _e, k=key: self._nav_paint(k))
        foot = tk.Frame(side, bg=SIDEBAR_BG)
        foot.pack(side="bottom", fill="x", padx=16, pady=16)
        self.status_dot = tk.Canvas(foot, width=14, height=14, highlightthickness=0, bg=SIDEBAR_BG)
        self.status_dot.pack(side="left", anchor="n", pady=1)
        tk.Label(foot, textvariable=self.status_var, bg=SIDEBAR_BG, fg=MUTED, wraplength=150, justify="left",
                 anchor="w", font=(FONT, 8)).pack(side="left", fill="x", padx=(6, 0))
        self.show_page("home")
        self._paint_page_labels(self.container)
        self.choose_frame.pack(fill="both", expand=True)

    PAGE_STYLES = ("Page.TLabel", "PageMuted.TLabel", "Title.TLabel", "Group.TLabel", "Chip.TLabel")

    def _paint_page_labels(self, widget) -> None:
        """sv-ttk draws every label with its theme colour whatever the style says; only the widget's
        own -background wins. Give it to the labels that sit on the darker page."""
        for child in widget.winfo_children():
            if isinstance(child, ttk.Label) and str(child.cget("style")) in self.PAGE_STYLES:
                child.configure(background=PAGE_BG)
            self._paint_page_labels(child)

    def show_page(self, key: str) -> None:
        self.page_var.set(key)
        for k, page in self.pages.items():
            if k == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
            self._nav_paint(k)

    def _nav_paint(self, key: str, hover: bool = False) -> None:
        item, bar_, pic, txt = self._nav[key]
        current = self.page_var.get() == key
        bg = NAV_ON if current else (NAV_HOVER if hover else SIDEBAR_BG)
        for w in (item, pic, txt):
            w.configure(bg=bg)
        bar_.configure(bg=ACCENT if current else bg)
        txt.configure(font=(FONT, 10, "bold") if current else (FONT, 10))

    # --- building blocks ----------------------------------------------------------------------
    def _icon_label(self, parent, name: str, size: int = 18, colour: str = TEXT, **kw) -> ttk.Label:
        img = self.icons.get(name, size, colour)
        return ttk.Label(parent, image=img or "", **kw)

    def _button(self, parent, text: str, icon: Optional[str] = None, command=None, accent: bool = False,
                tip: Optional[str] = None, style: Optional[str] = None, **kw) -> ttk.Button:
        colour = "#000000" if accent else TEXT
        img = self.icons.get(icon, 16, colour) if icon else None
        b = ttk.Button(parent, text=text, image=img or "", compound="left" if text else "image", command=command,
                       style=style or ("Accent.TButton" if accent else "TButton"), **kw)
        if tip:
            Tooltip(b, tip)
        return b

    def _back_button(self, parent) -> ttk.Button:
        return self._button(parent, "Back", "arrow-left", self._back_to_choice, style="Page.Toolbutton")

    def _title(self, parent, text: str, var: Optional[tk.StringVar] = None, pack: bool = True, size: int = 26) -> ttk.Label:
        """A page title drawn as an image (see Icons.text), following `var` when given."""
        lbl = ttk.Label(parent, style="Title.TLabel")

        def render(*_) -> None:
            t = var.get() if var is not None else text
            img = self.icons.text(t, size, TEXT)
            lbl.configure(image=img or "", text="" if img else t)
        render()
        if var is not None:
            var.trace_add("write", render)
        if pack:
            lbl.pack(anchor="w")
        return lbl

    def _card(self, parent, padding=16) -> tuple:
        """A rounded-looking surface (1 px border) on the page; returns (outer to pack, inner to fill)."""
        outer = tk.Frame(parent, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        inner = ttk.Frame(outer, padding=padding)
        inner.pack(fill="both", expand=True)
        return outer, inner

    def _group(self, parent, title: str) -> ttk.Frame:
        """A small caption over a card of settings rows; returns the card's inner frame."""
        ttk.Label(parent, text=title.upper(), style="Group.TLabel").pack(anchor="w", pady=(20, 6))
        outer, inner = self._card(parent, padding=(16, 6))
        outer.pack(fill="x")
        return inner

    def _row(self, card, icon: str, title: str, sub: Optional[str] = None, subvar: Optional[tk.StringVar] = None,
             tip: Optional[str] = None, first: bool = False) -> ttk.Frame:
        """One settings line: icon, title (and a short muted line), the control on the right."""
        if not first and card.winfo_children():
            ttk.Separator(card).pack(fill="x")
        row = ttk.Frame(card, padding=(0, 10))
        row.pack(fill="x")
        self._icon_label(row, icon, 18, MUTED).pack(side="left", padx=(0, 14))
        texts = ttk.Frame(row)
        texts.pack(side="left", fill="x", expand=True)
        t = ttk.Label(texts, text=title)
        t.pack(anchor="w")
        if sub or subvar is not None:
            ttk.Label(texts, text=sub or "", textvariable=subvar, style="Small.TLabel", wraplength=380, justify="left").pack(anchor="w")
        if tip:
            Tooltip(t, tip)
        right = ttk.Frame(row)
        right.pack(side="right")
        return right

    def _expander(self, card, title: str) -> ttk.Frame:
        """A collapsed line inside a card that opens the expert settings below it."""
        if card.winfo_children():
            ttk.Separator(card).pack(fill="x")
        holder = ttk.Frame(card)
        holder.pack(fill="x")
        body = ttk.Frame(holder, padding=(32, 2, 0, 12))
        closed, opened = self.icons.get("chevron-right", 14, MUTED), self.icons.get("chevron-down", 14, MUTED)
        head = ttk.Label(holder, text=("" if closed else "▸ ") + title, image=closed or "", compound="left",
                         style="Muted.TLabel", cursor="hand2", padding=(0, 9))

        def toggle(_e=None) -> None:
            if body.winfo_ismapped():
                body.pack_forget()
                head.configure(image=closed or "")
            else:
                body.pack(fill="x")
                head.configure(image=opened or "")
        head.pack(anchor="w")
        head.bind("<Button-1>", toggle)
        return body

    def _segmented(self, parent, var: tk.StringVar, options, command) -> ttk.Frame:
        """Toggle buttons side by side: one choice of a few (source, preset, mouse mode)."""
        seg = ttk.Frame(parent)
        for value, label, icon in options:
            ttk.Radiobutton(seg, text=label, value=value, variable=var, style="Toggle.TButton",
                            image=(self.icons.get(icon, 15) if icon else None) or "", compound="left",
                            command=command).pack(side="left", padx=(0, 4))
        return seg

    def _scrolling(self, parent) -> tuple:
        """A page whose content scrolls with the mouse wheel; returns (outer, content frame)."""
        outer = ttk.Frame(parent, style="Page.TFrame")
        canvas = tk.Canvas(outer, highlightthickness=0, bg=PAGE_BG)
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        f = ttk.Frame(canvas, padding=(32, 24, 32, 32), style="Page.TFrame")
        f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=f, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        def wheel(e) -> None:
            if f.winfo_ismapped() and canvas.winfo_height() < f.winfo_height():
                canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        outer.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
        outer.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        return outer, f

    CAMERA_CHOICES = ["Camera 0 (default)", "Camera 1", "Camera 2", "Camera 3", "Camera 4", "Camera 5"]

    def _camera_label(self, index: int) -> str:
        return self.CAMERA_CHOICES[index] if 0 <= index < len(self.CAMERA_CHOICES) else f"Camera {index}"

    def _camera_index(self) -> int:
        try:
            return int(self.cam_label.get().split()[1])
        except (IndexError, ValueError):
            return 0

    # --- first screens ------------------------------------------------------------------------
    def _build_choose(self, parent) -> ttk.Frame:
        """Launch screen: webcam (live preview) or phone, side by side."""
        last = self.app.profile.source
        f = ttk.Frame(parent, padding=40, style="Page.TFrame")
        self._title(f, "How do you track?", pack=False).pack(pady=(4, 24))
        cards = ttk.Frame(f, style="Page.TFrame")
        cards.pack()
        for col, kind in enumerate((SourceKind.WEBCAM, SourceKind.PHONE)):
            outer, c = self._card(cards, padding=20)
            outer.grid(row=0, column=col, padx=10, sticky="nsew")
            if kind is SourceKind.WEBCAM:
                self._blank_preview = tk.PhotoImage(width=288, height=216)
                self.choose_preview = tk.Label(c, bg="#000000", fg=MUTED, image=self._blank_preview, compound="center", text="Starting…", font=(FONT, 10))
                self.choose_preview.pack()
            else:
                self.choose_phone_art = tk.Canvas(c, width=288, height=216, bg="#000000", highlightthickness=0)
                self.choose_phone_art.pack()
            head = ttk.Frame(c)
            head.pack(fill="x", pady=(16, 2))
            self._icon_label(head, "webcam" if kind is SourceKind.WEBCAM else "smartphone", 20).pack(side="left", padx=(0, 8))
            ttk.Label(head, text="Webcam" if kind is SourceKind.WEBCAM else "Phone", style="Big.TLabel").pack(side="left")
            if kind is last:
                ttk.Label(head, text="last used", foreground=ACCENT, font=(FONT, 9)).pack(side="right")
            var = tk.StringVar(value="")
            ttk.Label(c, textvariable=var, style="Muted.TLabel", wraplength=290, justify="left").pack(anchor="w", pady=(0, 12))
            if kind is SourceKind.WEBCAM:
                self.choose_cam_var = var
                ttk.Combobox(c, textvariable=self.cam_label, values=self.CAMERA_CHOICES, width=18, state="readonly").pack(anchor="w", pady=(0, 12))
            else:
                self.choose_phone_var = var
                ttk.Label(c, text="HeadTrack Android app, same Wi-Fi", style="Small.TLabel").pack(anchor="w", pady=(0, 12))
            b = self._button(c, "Use webcam" if kind is SourceKind.WEBCAM else "Use phone", "arrow-right",
                             lambda k=kind: self._choose(k), accent=True)
            b.configure(compound="right")
            b.pack(side="bottom", fill="x")
        return f

    def _build_phone_wait(self, parent) -> ttk.Frame:
        """Phone chosen: this PC waits for it; the main window opens once it sends."""
        f = ttk.Frame(parent, padding=(24, 16, 24, 24), style="Page.TFrame")
        self._back_button(f).pack(anchor="w")
        mid = ttk.Frame(f, style="Page.TFrame")
        mid.pack(expand=True)
        self.phone_art = tk.Canvas(mid, width=300, height=150, bg=PAGE_BG, highlightthickness=0)
        self.phone_art.pack()
        self.phone_title = tk.StringVar(value="Waiting for the phone")
        self._title(mid, "", self.phone_title, pack=False).pack(pady=(6, 14))
        outer, c = self._card(mid, padding=(28, 16))
        outer.pack()
        self.phone_pc_name = tk.StringVar(value="")
        ttk.Label(c, textvariable=self.phone_pc_name, style="Big.TLabel").pack()
        self.phone_pc_addr = tk.StringVar(value="")
        ttk.Label(c, textvariable=self.phone_pc_addr, style="Muted.TLabel", font=("Consolas", 10)).pack(pady=(2, 0))
        ttk.Label(mid, text="Phone app → Connect → pick this PC", style="PageMuted.TLabel").pack(pady=(14, 4))
        self.phone_wait_status = tk.StringVar(value="")
        self.phone_wait_label = ttk.Label(mid, textvariable=self.phone_wait_status, style="PageMuted.TLabel", wraplength=520, justify="center")
        self.phone_wait_label.pack(pady=4)
        # shown only when Windows Firewall would block the phone's packets
        self.phone_fw, fw = self._card(mid, padding=(14, 10))
        self._icon_label(fw, "shield-alert", 20, WARN).pack(side="left", padx=(0, 10))
        ttk.Label(fw, text="Windows Firewall may block the phone").pack(side="left", padx=(0, 16))
        self._button(fw, "", "refresh-cw", self._check_firewall, tip="Check again").pack(side="right")
        self._button(fw, "Allow", "shield-check", self._allow_firewall, accent=True, tip="Adds a rule for UDP 4242/4244 (asks for admin)").pack(side="right", padx=6)
        self.phone_continue = self._button(mid, "Skip", None, self._skip_centre)
        self.phone_continue.pack(pady=(14, 0))
        return f

    def _choose(self, kind: SourceKind) -> None:
        self.source_var.set(kind.value)
        p = self.app.profile
        # saved even when unchanged: the chooser's webcam preview ran unsaved, the file may still say phone
        self.app.update_profile(replace(p, source=kind, camera=replace(p.camera, index=self._camera_index())))
        self.choose_frame.pack_forget()
        self._stage = kind.value
        if kind is SourceKind.PHONE:
            self._phone_fresh_since = None
            self.phone_frame.pack(fill="both", expand=True)
            self._check_firewall()
        else:
            self.centre_frame.pack(fill="both", expand=True)

    def _back_to_choice(self) -> None:
        self.centre_frame.pack_forget()
        self.phone_frame.pack_forget()
        self._stage = "choose"
        self.choose_frame.pack(fill="both", expand=True)
        # the chooser previews the webcam: start it, unsaved, so "last used" still means the last choice
        if self.app.profile.source is not SourceKind.WEBCAM:
            self.source_var.set(SourceKind.WEBCAM.value)
            self.app.update_profile(replace(self.app.profile, source=SourceKind.WEBCAM), save=False)

    def _check_firewall(self) -> None:
        from . import fixes
        self._firewall_ok = fixes.firewall_rule_present()
        if self._firewall_ok is False:
            if not self.phone_fw.winfo_ismapped():
                self.phone_fw.pack(pady=(10, 0), before=self.phone_continue)
        elif self.phone_fw.winfo_ismapped():
            self.phone_fw.pack_forget()

    def _allow_firewall(self) -> None:
        self._run_fix("firewall")
        self.root.after(4000, self._check_firewall)   # after the UAC prompt was answered, usually

    def _build_centre(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=(24, 16, 24, 24), style="Page.TFrame")
        self._back_button(f).pack(anchor="w")
        mid = ttk.Frame(f, style="Page.TFrame")
        mid.pack(expand=True)
        self._title(mid, "Set your centre", pack=False).pack()
        ttk.Label(mid, text="Sit as you play and look at the screen", style="PageMuted.TLabel").pack(pady=(2, 16))
        outer, c = self._card(mid, padding=16)
        outer.pack()
        self.centre_preview = tk.Label(c, bg="#000000", width=46, height=14)
        self.centre_preview.pack()
        # fills left to right during the 3 s countdown, breathes while measuring, empty otherwise
        self.centre_bar = tk.Canvas(c, width=320, height=4, highlightthickness=0, bg=CARD_BG)
        self.centre_bar.pack(pady=(10, 6))
        row = ttk.Frame(c)
        row.pack(fill="x")
        ttk.Combobox(row, textvariable=self.cam_label, values=self.CAMERA_CHOICES, width=18, state="readonly").pack(side="left")
        self._button(row, "Instant", "locate-fixed", self.app.recenter, tip="Use this pose as the centre now").pack(side="right")
        self.centre_btn = self._button(row, "Set centre", "crosshair", self.app.calibrate, accent=True, tip="Hold still for 3 seconds")
        self.centre_btn.pack(side="right", padx=8)
        self.centre_msg = tk.StringVar(value="")
        ttk.Label(mid, textvariable=self.centre_msg, foreground=ERR, background=PAGE_BG, wraplength=520).pack(pady=(8, 0))
        # shown only while the webcam has failed: what went wrong and what to do
        self.centre_problem, pc = self._card(mid, padding=(14, 10))
        top = ttk.Frame(pc)
        top.pack(fill="x")
        self._icon_label(top, "video-off", 18, ERR).pack(side="left", padx=(0, 10))
        self.centre_problem_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.centre_problem_var, wraplength=420, justify="left").pack(side="left")
        prow = ttk.Frame(pc)
        prow.pack(anchor="e", pady=(8, 0))
        self._button(prow, "Retry", "refresh-cw", self._retry_camera).pack(side="left")
        self._button(prow, "Use phone", "smartphone", self._use_phone).pack(side="left", padx=6)
        self._button(prow, "", "settings", lambda: self._run_fix("camera_privacy"), tip="Windows camera privacy settings").pack(side="left")
        self.centre_hint = tk.StringVar(value="")
        ttk.Label(mid, textvariable=self.centre_hint, style="PageMuted.TLabel").pack(pady=(8, 0))
        return f

    def _retry_camera(self) -> None:
        self.centre_problem_var.set("Restarting the camera…")
        self._run_fix("retry_camera")

    def _use_phone(self) -> None:
        self.centre_frame.pack_forget()
        self._choose(SourceKind.PHONE)

    # --- pages ------------------------------------------------------------------------------------
    PRESET_LABELS = {"driving": ("Drive", "car"), "flight": ("Fly", "plane"), "passthrough": ("1:1", "equal")}

    def _preset_options(self):
        return [(k, self.PRESET_LABELS.get(k, (p.name, None))[0], self.PRESET_LABELS.get(k, (p.name, None))[1])
                for k, p in PRESETS.items()]

    def _build_home(self, parent) -> ttk.Frame:
        """While playing: is it working, the view the game gets, recenter / pause / test, the feel."""
        f = ttk.Frame(parent, padding=(32, 24, 32, 24), style="Page.TFrame")
        self.track_state = tk.StringVar(value="")
        self._title(f, "", self.track_state)
        chips = ttk.Frame(f, style="Page.TFrame")
        chips.pack(anchor="w", pady=(4, 16))
        self.game_var = tk.StringVar(value="")
        ttk.Label(chips, textvariable=self.game_var, style="Chip.TLabel", image=self.icons.get("gamepad-2", 16, MUTED) or "",
                  compound="left").pack(side="left", padx=(0, 18))
        self.gaze_var = tk.StringVar(value="")
        ttk.Label(chips, textvariable=self.gaze_var, style="Chip.TLabel", image=self.icons.get("eye", 16, MUTED) or "",
                  compound="left").pack(side="left")
        # where the eyes look on the screen; shown only once the eyes are calibrated
        self.gaze_mini = tk.Canvas(chips, width=48, height=27, bg="#000000", highlightthickness=1, highlightbackground=BORDER)

        outer, c = self._card(f, padding=0)
        outer.pack(anchor="w")
        view = tk.Frame(c, bg="#000000")
        view.pack()
        self.cockpit = tk.Canvas(view, width=COCKPIT_W, height=COCKPIT_H, bg="#9ec5e8", highlightthickness=0)
        self.cockpit.pack()
        # the webcam picture in a corner of the view (hidden when the phone is the camera)
        self.preview_label = tk.Label(view, bg="#000000", fg=MUTED, borderwidth=0, font=(FONT, 9))
        self.preview_label.place(relx=1.0, rely=1.0, x=-10, y=-10, anchor="se")
        bar = ttk.Frame(c, padding=12)
        bar.pack(fill="x")
        self._button(bar, "Recenter", "crosshair", self.app.recenter, accent=True, tip="F12 or a wheel button").pack(side="left")
        self.pause_btn = self._button(bar, "Pause", "pause", self.app.engine.toggle_pause, tip="F11")
        self.pause_btn.pack(side="left", padx=6)
        self.sweep_btn = self._button(bar, "Test", "activity", self._toggle_sweep,
                                      tip="Turns the view right/left, up/down and tilts it, one at a time: watch the game follow")
        self.sweep_btn.pack(side="left")
        self._button(bar, "", "scan-face", self.app.calibrate, tip="Recalibrate the centre (3 s)").pack(side="left", padx=6)
        self.home_preset = tk.StringVar(value="")
        self._segmented(bar, self.home_preset, self._preset_options(), lambda: self._apply_preset(self.home_preset.get())).pack(side="right")

        self.sweep_var = tk.StringVar(value="")
        self.cockpit_words = tk.StringVar(value="")
        self.auto_var = tk.StringVar(value="")
        self.live_vars = {k: tk.StringVar(value="—") for k in ("raw", "calibrated", "output")}
        foot = ttk.Frame(f, style="Page.TFrame")
        foot.pack(fill="x", pady=(10, 0))
        ttk.Label(foot, textvariable=self.sweep_var, style="PageMuted.TLabel").pack(side="left")
        ttk.Label(f, textvariable=self.auto_var, style="PageMuted.TLabel").pack(anchor="w")
        return f

    def _build_phone(self, parent) -> ttk.Frame:
        outer, f = self._scrolling(parent)
        self._title(f, "Phone")
        o, c = self._card(f, padding=20)
        o.pack(fill="x", pady=(16, 0))
        self._icon_label(c, "monitor-smartphone", 40, ACCENT).pack(side="left", padx=(0, 18))
        texts = ttk.Frame(c)
        texts.pack(side="left", fill="x", expand=True)
        self.pc_name_var = tk.StringVar(value="")
        ttk.Label(texts, textvariable=self.pc_name_var, style="Big.TLabel").pack(anchor="w")
        self.pc_var = tk.StringVar(value="")
        ttk.Label(texts, textvariable=self.pc_var, style="Muted.TLabel", font=("Consolas", 10)).pack(anchor="w")
        self.phone_var = tk.StringVar(value="")
        ttk.Label(texts, textvariable=self.phone_var, wraplength=480, justify="left").pack(anchor="w", pady=(8, 0))

        c = self._group(f, "This PC")
        right = self._row(c, "monitor", "Name on the phone", "Empty = Windows name")
        self.name_var = tk.StringVar(value=self.app.profile.phone.pc_name)
        ttk.Entry(right, textvariable=self.name_var, width=20).pack(side="left", padx=6)
        self._button(right, "Save", None, self._apply_name).pack(side="left")
        right = self._row(c, "wifi", "Found automatically", "Answers the phone's search (UDP 4244)")
        self.discovery_var = tk.BooleanVar(value=self.app.profile.phone.discovery_enabled)
        ttk.Checkbutton(right, variable=self.discovery_var, style="Switch.TCheckbutton", command=self._apply_name).pack()
        body = self._expander(c, "How to connect")
        ttk.Label(body, wraplength=520, justify="left", text=(
            "1  Same Wi-Fi for phone and PC\n2  HeadTrack app → Connect → Find PC\n3  Tap this PC → Connect\n\n"
            "Not found? Help → Allow in firewall. Some routers keep Wi-Fi devices apart (AP isolation).")).pack(anchor="w")
        return outer

    def _build_settings(self, parent) -> ttk.Frame:
        outer, f = self._scrolling(parent)
        p = self.app.profile
        self._title(f, "Settings")

        c = self._group(f, "Tracking")
        right = self._row(c, "camera", "Source")
        self._segmented(right, self.source_var, [("webcam", "Webcam", "webcam"), ("phone", "Phone", "smartphone")],
                        self._apply_camera).pack()
        right = self._row(c, "webcam", "Camera")
        ttk.Combobox(right, textvariable=self.cam_label, values=self.CAMERA_CHOICES, width=18, state="readonly").pack()
        right = self._row(c, "flip-horizontal-2", "Mirrored image", tip="Turn on if left/right or the tilt are reversed")
        self.mirror_var = tk.BooleanVar(value=p.camera.mirrored)
        ttk.Checkbutton(right, variable=self.mirror_var, style="Switch.TCheckbutton", command=self._apply_camera).pack()

        c = self._group(f, "Feel")
        self.tuning_for = tk.StringVar(value=self._tuning_scope())
        right = self._row(c, "car", "Preset", subvar=self.tuning_for)
        self._segmented(right, self.home_preset, self._preset_options(), lambda: self._apply_preset(self.home_preset.get())).pack()
        right = self._row(c, "sliders-horizontal", "Smoothing", tip="Strength 0 (raw) to 1 (very smooth)")
        self.smooth_type = tk.StringVar(value=p.smoothing.type.name)
        ttk.Combobox(right, textvariable=self.smooth_type, values=[t.name for t in FilterType], width=11, state="readonly").pack(side="left")
        self.smooth_strength = tk.StringVar(value=f"{p.smoothing.strength:g}")
        ttk.Entry(right, textvariable=self.smooth_strength, width=5).pack(side="left", padx=6)
        self._button(right, "", "check", self._apply_tuning, tip="Apply").pack(side="left")
        box = self._expander(c, "Per axis")
        hdr = ("", "On", "Sensitivity", "Dead zone", "Max", "Curve", "Invert")
        for col, h in enumerate(hdr):
            ttk.Label(box, text=h, style="Small.TLabel").grid(row=0, column=col, padx=4, sticky="w")
        for r, axis in enumerate(Axis, start=1):
            s: AxisSettings = p.mapping.get(axis)
            vars_ = {
                "enabled": tk.BooleanVar(value=s.enabled), "sensitivity": tk.StringVar(value=f"{s.sensitivity:g}"),
                "dead_zone": tk.StringVar(value=f"{s.dead_zone:g}"), "max_output": tk.StringVar(value=f"{s.max_output:g}"),
                "curve": tk.StringVar(value=s.curve.name), "inverted": tk.BooleanVar(value=s.inverted),
            }
            self._axis_vars[axis.name] = vars_
            ttk.Label(box, text=f"{axis.label} ({axis.unit})").grid(row=r, column=0, sticky="w", padx=4, pady=2)
            ttk.Checkbutton(box, variable=vars_["enabled"]).grid(row=r, column=1)
            ttk.Entry(box, textvariable=vars_["sensitivity"], width=6).grid(row=r, column=2, padx=2)
            ttk.Entry(box, textvariable=vars_["dead_zone"], width=6).grid(row=r, column=3, padx=2)
            ttk.Entry(box, textvariable=vars_["max_output"], width=6).grid(row=r, column=4, padx=2)
            ttk.Combobox(box, textvariable=vars_["curve"], values=[cv.name for cv in ResponseCurve], width=9, state="readonly").grid(row=r, column=5, padx=2)
            ttk.Checkbutton(box, variable=vars_["inverted"]).grid(row=r, column=6)
        brow = ttk.Frame(box)
        brow.grid(row=10, column=0, columnspan=7, sticky="w", pady=(8, 0))
        self._button(brow, "Apply", "check", self._apply_tuning).pack(side="left")
        self._button(brow, "Reset", "rotate-ccw", self._reset_tuning, tip="Back to the driving defaults").pack(side="left", padx=6)
        self.tuning_msg = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.tuning_msg, foreground=ERR).grid(row=11, column=0, columnspan=7, sticky="w")

        c = self._group(f, "Controls")
        right = self._row(c, "locate-fixed", "Auto recenter", tip="When you sit still facing the screen for 2 s, the centre drifts to your resting pose")
        self.auto_centre_var = tk.BooleanVar(value=p.auto_centre.enabled)
        ttk.Checkbutton(right, variable=self.auto_centre_var, style="Switch.TCheckbutton", command=self._apply_recenter).pack()
        self.hotkey_sub = tk.StringVar(value=f"{p.hotkeys.recenter_key} recenter · {p.hotkeys.toggle_key or '—'} pause")
        right = self._row(c, "keyboard", "Hotkeys", subvar=self.hotkey_sub)
        self.hotkey_var = tk.BooleanVar(value=p.hotkeys.enabled)
        ttk.Checkbutton(right, variable=self.hotkey_var, style="Switch.TCheckbutton", command=self._apply_recenter).pack()
        box = self._expander(c, "Keys and wheel button")
        self.hotkey_key = tk.StringVar(value=p.hotkeys.recenter_key)
        self.toggle_key = tk.StringVar(value=p.hotkeys.toggle_key)
        self.joy_id = tk.StringVar(value=str(p.hotkeys.joystick_id))
        self.joy_btn = tk.StringVar(value=str(p.hotkeys.joystick_button))
        for r, (label, widget) in enumerate((("Recenter", ttk.Entry(box, textvariable=self.hotkey_key, width=10)),
                                             ("Pause", ttk.Entry(box, textvariable=self.toggle_key, width=10)))):
            ttk.Label(box, text=label).grid(row=r, column=0, sticky="w", pady=2)
            widget.grid(row=r, column=1, sticky="w", padx=8)
        ttk.Label(box, text="Wheel").grid(row=2, column=0, sticky="w", pady=2)
        jrow = ttk.Frame(box)
        jrow.grid(row=2, column=1, sticky="w", padx=8)
        ttk.Spinbox(jrow, from_=-1, to=15, textvariable=self.joy_id, width=4).pack(side="left")
        ttk.Label(jrow, text="button", style="Muted.TLabel").pack(side="left", padx=6)
        ttk.Spinbox(jrow, from_=-1, to=31, textvariable=self.joy_btn, width=4).pack(side="left")
        self._button(box, "Apply", "check", self._apply_recenter).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.hotkey_msg = tk.StringVar(value="F1–F24, A–Z, 0–9, Space, Home, End, Insert, Pause · wheel -1 = none")
        ttk.Label(box, textvariable=self.hotkey_msg, style="Small.TLabel", wraplength=480, justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        c = self._group(f, "Eyes")
        self.screen_gaze_var = tk.StringVar(value="")
        right = self._row(c, "scan-eye", "Eye calibration", subvar=self.screen_gaze_var, tip="Look at 9 dots (about 20 s)")
        self._button(right, "Calibrate", None, self._start_screen_calibration, accent=True).pack(side="left")
        self._button(right, "", "x", self.app.engine.clear_screen_calibration, tip="Clear").pack(side="left", padx=(6, 0))
        right = self._row(c, "eye", "Eyes move the camera", tip="A glance turns the game camera a little further than your head")
        self.ext_var = tk.BooleanVar(value=p.eye_assist.enabled and p.eye_assist.source is GazeSource.SCREEN)
        ttk.Checkbutton(right, variable=self.ext_var, style="Switch.TCheckbutton", command=self._apply_extended).pack()
        box = self._expander(c, "Fine-tune")
        row = ttk.Frame(box)
        row.pack(anchor="w")
        self.ext_vertical = tk.BooleanVar(value=p.eye_assist.vertical)
        ttk.Checkbutton(row, text="Up/down too", variable=self.ext_vertical, command=self._apply_extended).pack(side="left")
        ttk.Label(row, text="extra °  ↔", style="Muted.TLabel").pack(side="left", padx=(14, 4))
        self.ext_gain = tk.StringVar(value=f"{p.eye_assist.gain_degrees:g}")
        ttk.Entry(row, textvariable=self.ext_gain, width=5).pack(side="left")
        ttk.Label(row, text="↕", style="Muted.TLabel").pack(side="left", padx=4)
        self.ext_gain_y = tk.StringVar(value=f"{p.eye_assist.gain_degrees_y:g}")
        ttk.Entry(row, textvariable=self.ext_gain_y, width=5).pack(side="left")
        self._button(row, "", "check", self._apply_extended, tip="Apply").pack(side="left", padx=6)
        ttk.Label(box, text="Glance without calibration (experimental)", style="Small.TLabel").pack(anchor="w", pady=(12, 2))
        row = ttk.Frame(box)
        row.pack(anchor="w")
        self.eye_var = tk.BooleanVar(value=p.eye_assist.enabled)
        ttk.Checkbutton(row, text="On", variable=self.eye_var, command=self._apply_eye).pack(side="left")
        self.eye_source = tk.StringVar(value=p.eye_assist.source.name)
        ttk.Combobox(row, textvariable=self.eye_source, values=[gs.name for gs in GazeSource], width=7, state="readonly").pack(side="left", padx=6)
        self.eye_dead = tk.StringVar(value=f"{p.eye_assist.dead_zone:g}")
        self.eye_gain = tk.StringVar(value=f"{p.eye_assist.gain_degrees:g}")
        self.eye_max = tk.StringVar(value=f"{p.eye_assist.max_degrees:g}")
        for label, var in (("dead", self.eye_dead), ("gain°", self.eye_gain), ("max°", self.eye_max)):
            ttk.Label(row, text=label, style="Muted.TLabel").pack(side="left", padx=(6, 2))
            ttk.Entry(row, textvariable=var, width=5).pack(side="left")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(4, 0))
        self.eye_comp = tk.BooleanVar(value=p.eye_assist.head_compensation)
        ttk.Checkbutton(row, text="Head-turn compensation", variable=self.eye_comp, command=self._apply_eye).pack(side="left")
        self._button(row, "Calibrate 6 s", None, self.app.engine.start_gaze_calibration).pack(side="left", padx=6)
        self._button(row, "", "check", self._apply_eye, tip="Apply").pack(side="left")
        self.eye_msg = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.eye_msg, style="Small.TLabel", wraplength=480, justify="left").pack(anchor="w")

        c = self._group(f, "Mouse")
        self.mouse_mode = tk.StringVar(value=p.mouse.mode.value)
        self.mouse_msg = tk.StringVar(value="")
        right = self._row(c, "mouse-pointer-2", "Mouse", subvar=self.mouse_msg, tip="For games without TrackIR, or a cursor that follows your eyes")
        self._segmented(right, self.mouse_mode, [(MouseMode.OFF.value, "Off", None), (MouseMode.HEAD.value, "Head", None),
                                                 (MouseMode.GAZE_FOLLOW.value, "Eyes", None), (MouseMode.GAZE_HOTKEY.value, "Eyes + key", None)],
                        self._apply_mouse).pack()
        box = self._expander(c, "Speed and keys")
        row = ttk.Frame(box)
        row.pack(anchor="w")
        ttk.Label(row, text="px/° ↔ ↕", style="Muted.TLabel").pack(side="left")
        self.mouse_ppd_x = tk.StringVar(value=f"{p.mouse.pixels_per_degree_x:g}")
        self.mouse_ppd_y = tk.StringVar(value=f"{p.mouse.pixels_per_degree_y:g}")
        ttk.Entry(row, textvariable=self.mouse_ppd_x, width=5).pack(side="left", padx=4)
        ttk.Entry(row, textvariable=self.mouse_ppd_y, width=5).pack(side="left")
        self.mouse_inv = tk.BooleanVar(value=p.mouse.invert_y)
        ttk.Checkbutton(row, text="Invert ↕", variable=self.mouse_inv).pack(side="left", padx=12)
        ttk.Label(row, text="jump key", style="Muted.TLabel").pack(side="left")
        self.warp_key = tk.StringVar(value=p.hotkeys.gaze_warp_key)
        ttk.Entry(row, textvariable=self.warp_key, width=7).pack(side="left", padx=4)
        self._button(row, "", "check", self._apply_mouse, tip="Apply").pack(side="left", padx=4)

        c = self._group(f, "Output")
        self.output_notes = tk.StringVar(value="")
        right = self._row(c, "gamepad-2", "Send to games", tip="TrackIR / FreeTrack: every game that supports either")
        self.ft_var = tk.BooleanVar(value=p.output.freetrack_enabled)
        ttk.Checkbutton(right, variable=self.ft_var, style="Switch.TCheckbutton", command=self._apply_output).pack()
        box = self._expander(c, "Interface and opentrack UDP")
        row = ttk.Frame(box)
        row.pack(anchor="w")
        self.ft_iface = tk.StringVar(value=p.output.freetrack_interface.value)
        self._segmented(row, self.ft_iface, [("both", "Both", None), ("npclient", "TrackIR", None), ("freetrack", "FreeTrack", None)],
                        self._apply_output).pack(side="left")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(8, 0))
        self.udp_var = tk.BooleanVar(value=p.output.udp_enabled)
        ttk.Checkbutton(row, text="UDP to", variable=self.udp_var, command=self._apply_output).pack(side="left")
        self.udp_host = tk.StringVar(value=p.output.udp_host)
        self.udp_port = tk.StringVar(value=str(p.output.udp_port))
        ttk.Entry(row, textvariable=self.udp_host, width=15).pack(side="left", padx=4)
        ttk.Entry(row, textvariable=self.udp_port, width=6).pack(side="left")
        self._button(row, "", "check", self._apply_output, tip="Apply").pack(side="left", padx=6)
        ttk.Label(box, textvariable=self.output_notes, style="Small.TLabel", wraplength=480, justify="left").pack(anchor="w", pady=(6, 0))
        self.api_msg = tk.StringVar(value="")
        right = self._row(c, "cast", "Stream overlay and API", subvar=self.api_msg, tip="A gaze bubble for OBS (Browser source) and head/gaze data at /state.json")
        self._button(right, "", "copy", self._copy_overlay, tip="Copy the overlay URL").pack(side="left")
        self._button(right, "", "external-link", self._open_api, tip="Open in the browser").pack(side="left", padx=6)
        self.api_var = tk.BooleanVar(value=p.api.enabled)
        ttk.Checkbutton(right, variable=self.api_var, style="Switch.TCheckbutton", command=self._apply_api).pack(side="left")
        box = self._expander(c, "Port")
        row = ttk.Frame(box)
        row.pack(anchor="w")
        self.api_port = tk.StringVar(value=str(p.api.port))
        ttk.Entry(row, textvariable=self.api_port, width=7).pack(side="left")
        self._button(row, "", "check", self._apply_api, tip="Apply").pack(side="left", padx=6)
        return outer

    def _build_help(self, parent) -> ttk.Frame:
        outer, f = self._scrolling(parent)
        self._title(f, "Help")
        self.check_headline = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.check_headline, style="PageMuted.TLabel").pack(anchor="w", pady=(2, 0))
        o, c = self._card(f, padding=(16, 8))
        o.pack(fill="x", pady=(16, 0))
        self.check_rows = ttk.Frame(c)
        self.check_rows.pack(fill="x")
        self.check_rows.columnconfigure(2, weight=1)
        self._check_widgets: Dict[str, tuple] = {}
        self.fix_msg = tk.StringVar(value="")
        ttk.Label(c, textvariable=self.fix_msg, foreground=OK, wraplength=560, justify="left").pack(anchor="w")

        c = self._group(f, "Games")
        row = ttk.Frame(c, padding=(0, 8))
        row.pack(fill="x")
        self._icon_label(row, "search", 16, MUTED).pack(side="left", padx=(0, 8))
        self.game_search = tk.StringVar(value="")
        e = ttk.Entry(row, textvariable=self.game_search)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<KeyRelease>", lambda _e: self._fill_games())
        self.games_list = tk.Listbox(c, height=8, bg=CARD_BG, fg=TEXT, highlightthickness=0, borderwidth=0,
                                     selectbackground="#2f60d8", activestyle="none", font=(FONT, 10))
        self.games_list.pack(fill="x", pady=(0, 8))
        self._fill_games()
        ttk.Label(c, text="Any TrackIR or FreeTrack game works, listed or not.", style="Small.TLabel").pack(anchor="w", pady=(0, 8))

        c = self._group(f, "Details")
        body = self._expander(c, "Technical report")
        grid = ttk.Frame(body)
        grid.pack(anchor="w")
        for i, (k, label) in enumerate((("raw", "Camera"), ("calibrated", "Centred"), ("output", "Sent"))):
            ttk.Label(grid, text=label, style="Muted.TLabel", width=9).grid(row=i, column=0, sticky="w")
            ttk.Label(grid, textvariable=self.live_vars[k], font=("Consolas", 9)).grid(row=i, column=1, sticky="w")
        self.diag_var = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.diag_var, font=("Consolas", 9), justify="left").pack(anchor="w", pady=(6, 0))
        self._button(body, "Copy report", "copy", self._copy_report).pack(anchor="w", pady=(8, 0))
        steam_line = f" · {self.steam.status.message}" if self.steam is not None else ""
        ttk.Label(f, style="PageMuted.TLabel", wraplength=600, justify="left", font=(FONT, 9), text=(
            f"HeadTrack PC {__version__}{steam_line} · MediaPipe (Apache 2.0) · opentrack client DLLs (ISC) · "
            "Lucide icons (ISC) · road photo Poly Haven (CC0)")).pack(anchor="w", pady=(20, 0))
        return outer

    def _toggle_sweep(self) -> None:
        if self.app.engine.sweeping:
            self.app.engine.stop_sweep()
        else:
            self.app.engine.start_sweep()

    def _draw_cockpit(self, st: EngineState, now: float, dt: float) -> None:
        target = sim.camera(st.output)
        # the camera glides to the sent pose (120 ms time constant): smooth to watch, still immediate
        e = self._cam
        pan_x, pan_y = e["pan_x"].step(target.pan_x, dt), e["pan_y"].step(target.pan_y, dt)
        roll, zoom = e["roll"].step(target.roll_degrees, dt), e["zoom"].step(target.zoom, dt)
        parallax_x, parallax_y = e["parallax_x"].step(target.parallax_x, dt), e["parallax_y"].step(target.parallax_y, dt)
        self.cockpit_words.set("Looking at: " + target.words)
        if self._scene is not None:
            # the road photo: redrawn only when the glide moved it, so a still head costs nothing
            key = tuple(round(v, 4) for v in (pan_x, pan_y, roll, zoom, parallax_x, parallax_y))
            if key != self._scene_key:
                self._scene_key = key
                from PIL import ImageTk
                img = self._scene.render(-pan_x * sim.FOV_H, pan_y * sim.FOV_V, -roll, zoom, parallax_x, parallax_y)
                self._scene_photo = ImageTk.PhotoImage(img)
                self.cockpit.delete("all")
                self.cockpit.create_image(0, 0, image=self._scene_photo, anchor="nw")
            return
        c = self.cockpit
        w, h = COCKPIT_W, COCKPIT_H
        k = w / 360.0
        c.delete("all")
        cx, cy = w / 2 + pan_x * w, h / 2 + pan_y * h
        ang = math.radians(roll)
        cos_a, sin_a = math.cos(ang), math.sin(ang)

        def rot(x, y):
            return cx + (x * cos_a - y * sin_a) * zoom * k, cy + (x * sin_a + y * cos_a) * zoom * k
        # far scene: sky/ground split by the horizon, a road converging to the vanishing point
        far = [rot(-700, 0), rot(700, 0), rot(700, 600), rot(-700, 600)]
        c.create_polygon(*[v for p in far for v in p], fill="#5b8c3a", outline="")
        c.create_oval(*rot(-470, -130), *rot(-410, -70), fill="#fde68a", outline="")   # low sun to the left
        c.create_polygon(*[v for p in [rot(-40, 0), rot(40, 0), rot(360, 600), rot(-360, 600)] for v in p], fill="#555", outline="")
        # centre dashes scroll toward the car so the scene reads as driving (depth t → distance t²)
        for t0, t1 in anim.road_dashes(anim.cycle(now, ROAD_PERIOD), count=8, length=0.4):
            c.create_line(*rot(0, 240 * t0 * t0), *rot(0, 240 * t1 * t1), fill="#eee", width=max(1.0, (1 + 4 * t1) * zoom))
        for x in (-220, -120, 120, 220):
            c.create_rectangle(*rot(x - 10, -60), *rot(x + 10, 0), fill="#8a6", outline="")
        # near cockpit (dashboard, pillars, mirrors) moves with parallax and does not roll with the world
        px, py = parallax_x * w, parallax_y * h
        c.create_rectangle(0 + px, h * 0.72 + py, w + px, h + py, fill="#2b2b2b", outline="")
        c.create_rectangle(-20 + px, 0 + py, 30 + px, h + py, fill="#1e1e1e", outline="")
        c.create_rectangle(w - 30 + px, 0 + py, w + 20 + px, h + py, fill="#1e1e1e", outline="")
        c.create_rectangle(w * 0.42 + px, 6 + py, w * 0.58 + px, 26 + py, fill="#111", outline="#888")
        c.create_oval(w * 0.3 + px, h * 0.6 + py, w * 0.7 + px, h * 1.3 + py, outline="#777", width=6)

    # --- actions ----------------------------------------------------------------------------------
    def _skip_centre(self) -> None:
        self._centre_done = True
        self._show_tabs()

    def _show_tabs(self) -> None:
        """Slide the tabs in from the right over the centre screen, then hand the layout back to pack."""
        if self._tabs_shown:
            return
        self._tabs_shown = True
        self._slide.start(time.monotonic())
        self.main.place(relx=1.0, rely=0.0, relwidth=1.0, relheight=1.0)
        self.main.lift()

    def _finish_slide(self) -> None:
        self.main.place_forget()
        for frame in (self.choose_frame, self.centre_frame, self.phone_frame):
            frame.pack_forget()
        self.main.pack(fill="both", expand=True)

    def _start_fade(self) -> None:
        """Fade the window in at launch where the platform supports it (Windows, compositing X11, macOS)."""
        try:
            self.root.attributes("-alpha", 0.0)
            self._fade_armed = True
        except tk.TclError:
            self._fade_armed = False

    def _set_alpha(self, window, alpha: float) -> None:
        try:
            window.attributes("-alpha", max(0.0, min(1.0, alpha)))
        except tk.TclError:
            pass

    def _apply_name(self) -> None:
        p = self.app.profile
        self.app.update_profile(replace(p, phone=replace(p.phone, pc_name=self.name_var.get().strip(),
                                                         discovery_enabled=bool(self.discovery_var.get()))))

    def _apply_output(self) -> None:
        p = self.app.profile
        try:
            port = int(self.udp_port.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.output_notes.set("UDP port must be 1..65535")
            return
        self.app.update_profile(replace(p, output=replace(
            p.output, freetrack_enabled=bool(self.ft_var.get()), freetrack_interface=FreetrackInterface(self.ft_iface.get()),
            udp_enabled=bool(self.udp_var.get()), udp_host=self.udp_host.get().strip() or "127.0.0.1", udp_port=port)))
        self.output_notes.set("\n".join(self.app.output_notes))

    def _apply_camera(self) -> None:
        p = self.app.profile
        mirrored = bool(self.mirror_var.get()) if hasattr(self, "mirror_var") else p.camera.mirrored
        new = replace(p, source=SourceKind(self.source_var.get()), camera=replace(p.camera, index=self._camera_index(), mirrored=mirrored))
        if new != p:
            self.app.update_profile(new)

    def _apply_tuning(self) -> None:
        p = self.app.profile
        mapping = p.mapping
        try:
            for axis in Axis:
                v = self._axis_vars[axis.name]
                mapping = mapping.with_axis(axis, AxisSettings(
                    enabled=bool(v["enabled"].get()), sensitivity=float(v["sensitivity"].get().replace(",", ".")),
                    inverted=bool(v["inverted"].get()), dead_zone=float(v["dead_zone"].get().replace(",", ".")),
                    max_output=float(v["max_output"].get().replace(",", ".")), curve=ResponseCurve[v["curve"].get()]))
            smoothing = replace(p.smoothing, type=FilterType[self.smooth_type.get()],
                                strength=float(self.smooth_strength.get().replace(",", ".")))
        except (ValueError, KeyError) as e:
            self.tuning_msg.set(f"Check the numbers: {e}")
            return
        self.tuning_msg.set("")
        self.app.update_profile(replace(p, mapping=mapping, smoothing=smoothing))

    def _apply_preset(self, key: str) -> None:
        self.app.apply_preset(key)
        self._load_tuning_fields()

    def _load_tuning_fields(self) -> None:
        p = self.app.profile
        for axis in Axis:
            s = p.mapping.get(axis)
            v = self._axis_vars[axis.name]
            v["enabled"].set(s.enabled); v["sensitivity"].set(f"{s.sensitivity:g}"); v["dead_zone"].set(f"{s.dead_zone:g}")
            v["max_output"].set(f"{s.max_output:g}"); v["curve"].set(s.curve.name); v["inverted"].set(s.inverted)
        self.smooth_type.set(p.smoothing.type.name)
        self.smooth_strength.set(f"{p.smoothing.strength:g}")
        self.tuning_for.set(self._tuning_scope())

    def _tuning_scope(self) -> str:
        """Which games an edit applies to, in a few words (the long form is app.tuning_label())."""
        if self.app.active_game_id > 0:
            return f"Saved for {self.app.active_game_name}"
        return "All games without their own"

    def _apply_recenter(self) -> None:
        p = self.app.profile
        key = self.hotkey_key.get().strip()
        if self.hotkey_var.get() and key and parse_key(key) is None:
            self.hotkey_msg.set(f"Unknown key '{key}'. Use F1–F24, A–Z, 0–9, Space, Home, End, Insert, Pause.")
            return
        try:
            jid, jbtn = int(self.joy_id.get()), int(self.joy_btn.get())
        except ValueError:
            jid, jbtn = -1, -1
        toggle = self.toggle_key.get().strip()
        if toggle and parse_key(toggle) is None:
            self.hotkey_msg.set(f"Unknown pause key '{toggle}'.")
            return
        self.app.update_profile(replace(p, auto_centre=replace(p.auto_centre, enabled=bool(self.auto_centre_var.get())),
                                        hotkeys=replace(p.hotkeys, enabled=bool(self.hotkey_var.get()), recenter_key=key or "F12",
                                                        joystick_id=jid, joystick_button=jbtn, toggle_key=toggle)))
        self.hotkey_msg.set("Applied." + (f" {self.app.hotkeys.error}" if self.app.hotkeys.error else ""))
        self.hotkey_sub.set(f"{key or 'F12'} recenter · {toggle or '—'} pause")

    def _apply_eye(self) -> None:
        p = self.app.profile
        try:
            ea = replace(p.eye_assist, enabled=bool(self.eye_var.get()), source=GazeSource[self.eye_source.get()],
                         dead_zone=float(self.eye_dead.get().replace(",", ".")), gain_degrees=float(self.eye_gain.get().replace(",", ".")),
                         max_degrees=float(self.eye_max.get().replace(",", ".")), head_compensation=bool(self.eye_comp.get()))
        except (ValueError, KeyError) as e:
            self.eye_msg.set(f"Check the numbers: {e}")
            return
        self.app.update_profile(replace(p, eye_assist=ea))
        self.eye_msg.set("Applied. The camera restarts with the eye model when turning this on or off.")

    def _apply_extended(self) -> None:
        p = self.app.profile
        try:
            gain = float(self.ext_gain.get().replace(",", "."))
            gain_y = float(self.ext_gain_y.get().replace(",", "."))
        except ValueError:
            self.screen_gaze_var.set("Check the gain numbers")
            return
        on = bool(self.ext_var.get())
        ea = replace(p.eye_assist, enabled=on or (p.eye_assist.enabled and p.eye_assist.source is not GazeSource.SCREEN),
                     source=GazeSource.SCREEN if on else p.eye_assist.source, vertical=bool(self.ext_vertical.get()),
                     gain_degrees=gain, max_degrees=max(gain, p.eye_assist.max_degrees) if on else p.eye_assist.max_degrees,
                     gain_degrees_y=gain_y, max_degrees_y=max(gain_y, p.eye_assist.max_degrees_y))
        self.app.update_profile(replace(p, eye_assist=ea))
        self.eye_var.set(ea.enabled)
        self.eye_source.set(ea.source.name)

    def _apply_api(self) -> None:
        p = self.app.profile
        try:
            port = int(self.api_port.get())
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.api_msg.set("Port must be 1024..65535")
            return
        self.app.update_profile(replace(p, api=ApiSettings(enabled=bool(self.api_var.get()), port=port, bind=p.api.bind)))
        self._refresh_api_msg()

    def _refresh_api_msg(self) -> None:
        s = self.app.server
        if s is None:
            self.api_msg.set("Off")
        elif s.error:
            self.api_msg.set(s.error)
        else:
            self.api_msg.set(f"{s.url.rstrip('/')} · {s.requests} requests")

    def _copy_overlay(self) -> None:
        s = self.app.server
        if s is None or s.error:
            self.api_msg.set("Turn the API on first")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(f"{s.url}overlay.html")
        self.api_msg.set("Overlay URL copied: paste it into an OBS Browser Source")

    def _open_api(self) -> None:
        s = self.app.server
        if s is not None and not s.error:
            import webbrowser
            webbrowser.open(s.url)

    def _apply_mouse(self) -> None:
        p = self.app.profile
        try:
            ms = MouseSettings(mode=MouseMode(self.mouse_mode.get()), pixels_per_degree_x=float(self.mouse_ppd_x.get().replace(",", ".")),
                               pixels_per_degree_y=float(self.mouse_ppd_y.get().replace(",", ".")), invert_y=bool(self.mouse_inv.get()),
                               dead_zone_degrees=p.mouse.dead_zone_degrees, gaze_smoothing=p.mouse.gaze_smoothing)
        except ValueError as e:
            self.mouse_msg.set(f"Check the numbers: {e}")
            return
        warp = self.warp_key.get().strip()
        if warp and parse_key(warp) is None:
            self.mouse_msg.set(f"Unknown jump key '{warp}'")
            return
        self.app.update_profile(replace(p, mouse=ms, hotkeys=replace(p.hotkeys, gaze_warp_key=warp)))
        note = next((n for n in self.app.output_notes if "Mouse" in n), "Mouse off")
        self.mouse_msg.set(note + ("  Gaze modes need the eye calibration above." if ms.mode in (MouseMode.GAZE_FOLLOW, MouseMode.GAZE_HOTKEY) and not p.screen_gaze.weights else ""))

    def _fill_games(self) -> None:
        q = self.game_search.get().strip().lower()
        self.games_list.delete(0, "end")
        shown = 0
        for g in sorted(game_table.load_games().values(), key=lambda g: g.name.lower()):
            if q and q not in g.name.lower():
                continue
            self.games_list.insert("end", f"{g.name}   (ID {g.game_id}, {category_of(g.game_id)} preset)")
            shown += 1
            if shown >= 200:
                self.games_list.insert("end", "… type to narrow the list")
                break

    def _start_screen_calibration(self) -> None:
        if self._cal_window is not None:
            return
        self.app.start_screen_calibration()
        win = tk.Toplevel(self.root)
        win.attributes("-fullscreen", True)
        win.attributes("-topmost", True)
        win.configure(bg="#111")
        c = tk.Canvas(win, bg="#111", highlightthickness=0)
        c.pack(fill="both", expand=True)
        win.bind("<Escape>", lambda _e: self._end_screen_calibration(cancel=True))
        self._cal_window = (win, c)
        self._cal_dot = None
        self._cal_last = time.monotonic()
        self._cal_fade = anim.Transition(0.2)
        self._cal_fade.start(self._cal_last)
        self._set_alpha(win, 0.0)
        self.root.after(50, self._draw_calibration)

    def _end_screen_calibration(self, cancel: bool = False) -> None:
        if cancel:
            self.app.engine.cancel_screen_calibration()
        if self._cal_window is not None:
            win, _ = self._cal_window
            try:
                win.destroy()
            except Exception:
                pass
            self._cal_window = None

    def _draw_calibration(self) -> None:
        if self._cal_window is None:
            return
        win, c = self._cal_window
        st = self.app.engine.state.screen_calibration
        if st is None or st.phase in ("done", "failed"):
            self._end_screen_calibration()
            return
        w, h = max(1, c.winfo_width()), max(1, c.winfo_height())
        now = time.monotonic()
        dt, self._cal_last = min(0.25, now - self._cal_last), now
        self._set_alpha(win, self._cal_fade.progress(now))
        if self._cal_dot is None:   # first frame: start on the first point, no glide
            self._cal_dot = (anim.Eased(st.point[0], seconds=0.12, snap=1e-4), anim.Eased(st.point[1], seconds=0.12, snap=1e-4))
        ex, ey = self._cal_dot
        x, y = ex.step(st.point[0], dt) * w, ey.step(st.point[1], dt) * h
        c.delete("all")
        settling = st.phase == "settle"
        r = 22 if settling else 14
        ring = r + 8 + (4 * anim.pulse(now, 1.0) if settling else 0)   # breathes while the eyes settle, steady while sampling
        c.create_oval(x - ring, y - ring, x + ring, y + ring, outline=ACCENT, width=2)
        c.create_oval(x - r, y - r, x + r, y + r, fill=ACCENT if st.phase == "sample" else "#357", outline="")
        c.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#fff", outline="")
        c.create_arc(x - r - 14, y - r - 14, x + r + 14, y + r + 14, start=90, extent=-360 * st.progress, style="arc", outline="#fff", width=3)
        c.create_text(w / 2, h - 40, fill="#ccc", font=("", 14), text=f"Look at the dot and keep your head still  ·  point {st.index + 1} of {st.total}  ·  Esc cancels")
        self.root.after(33, self._draw_calibration)

    def _draw_gaze_mini(self, st: EngineState, dt: float) -> None:
        c = self.gaze_mini
        c.delete("all")
        g = st.gaze_point
        if g is None:
            self._gaze_seen = False
            return
        ex, ey = self._gaze_dot
        tx, ty = max(0.0, min(1.0, g.x)), max(0.0, min(1.0, g.y))
        if not self._gaze_seen:   # first point after a gap: appear there, do not glide in from the old spot
            ex.jump(tx)
            ey.jump(ty)
            self._gaze_seen = True
        x, y = ex.step(tx, dt) * 64, ey.step(ty, dt) * 36
        c.create_oval(x - 5, y - 5, x + 5, y + 5, fill=ACCENT if g.on_screen else "#a55", outline="")

    def _run_fix(self, action: str) -> None:
        self.fix_msg.set(self.app.run_fix(action))

    def _reset_tuning(self) -> None:
        self._apply_preset("driving")

    def _copy_report(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.diag_var.get() + "\n" + "\n".join(self.app.output_notes))

    def close(self) -> None:
        try:
            self.app.stop()
        finally:
            self.root.destroy()

    # --- animation ---------------------------------------------------------------------------------
    def _animate(self) -> None:
        """~30 fps: the few things that move between engine polls. Each part redraws only when visible."""
        now = time.monotonic()
        dt, self._anim_last = min(0.25, now - self._anim_last), now
        try:
            if self._fade_armed:   # first tick after the window is up, not at construction time
                self._fade_armed = False
                self._fade.start(now)
            if self._fade.started:
                self._set_alpha(self.root, self._fade.progress(now))
                if self._fade.finished(now):
                    self._fade.reset()
            if self._slide.active(now):
                self.main.place_configure(relx=1.0 - self._slide.progress(now))
            elif self._slide.started:
                self._finish_slide()
                self._slide.reset()
            st = self._last_state
            if st is not None:
                self._draw_status_dot(st, now)
                if self.centre_bar.winfo_viewable():
                    self._draw_centre_bar(st, now, dt)
                if self.choose_phone_art.winfo_viewable():
                    self._draw_phone_art(self.choose_phone_art, now, st.phone_fresh, "#222")
                if self.phone_art.winfo_viewable():
                    self._draw_phone_art(self.phone_art, now, st.phone_fresh, self._frame_bg)
                if self.cockpit.winfo_viewable():
                    self._draw_cockpit(st, now, dt)
                if self.gaze_mini.winfo_viewable():
                    self._draw_gaze_mini(st, dt)
        except tk.TclError:
            pass   # a widget went away while closing
        except Exception:
            # never leave the window half faded or the tabs half slid: finish the layout and stop animating
            import logging
            logging.getLogger("headtrack").exception("animation stopped")
            self._fade_armed = False
            self._set_alpha(self.root, 1.0)
            if self._slide.started:
                self._finish_slide()
                self._slide.reset()
            return
        self.root.after(ANIM_MS, self._animate)

    def _draw_status_dot(self, st: EngineState, now: float) -> None:
        """One dot that says how tracking is: green and breathing while a face is tracked."""
        live = False
        if st.phone_fresh:
            colour, live = "#36c", True
        elif st.webcam_status is SourceStatus.FAILED or st.output_errors:
            colour = "#c33"
        elif st.webcam_status is SourceStatus.STARTING:
            colour, live = "#c90", True
        elif st.webcam_status is SourceStatus.RUNNING and st.tracking is TrackingState.TRACKING and not st.paused:
            colour, live = "#2a2", True
        elif st.webcam_status is SourceStatus.RUNNING:
            colour = "#c90"
        else:
            colour = "#999"
        r = 4.0 + (1.5 * anim.pulse(now, 2.0) if live else 0.0)
        c = self.status_dot
        c.delete("all")
        c.create_oval(7 - r, 7 - r, 7 + r, 7 + r, fill=colour, outline="")

    def _draw_centre_bar(self, st: EngineState, now: float, dt: float) -> None:
        countdown = self.app.profile.calibration.countdown_millis / 1000.0
        if st.calibration is CalibrationPhase.COUNTDOWN and countdown > 0:
            target = 1.0 - st.calibration_seconds_left / countdown
        elif st.calibration is CalibrationPhase.SAMPLING:
            target = 1.0
        else:
            target = 0.0
        fill = self._centre_fill.step(target, dt)
        c = self.centre_bar
        c.delete("all")
        if fill > 0.0:
            colour = ACCENT
            if st.calibration is CalibrationPhase.SAMPLING:
                shade = int(0x99 + 0x66 * anim.pulse(now, 0.8))   # breathes while the samples are taken
                colour = f"#55{shade:02x}ff"
            c.create_rectangle(0, 0, 320 * fill, 4, fill=colour, outline="")

    # --- polling ----------------------------------------------------------------------------------
    def _poll(self) -> None:
        st = self.app.engine.state
        self._last_state = st
        self._update_status(st)
        self._update_choose(st)
        self._update_centre(st)
        self._update_phone_wait(st)
        self._update_track(st)
        self._update_connect(st)
        self._update_diagnostics(st)
        self.root.after(POLL_MS, self._poll)

    def _update_status(self, st: EngineState) -> None:
        parts = []
        if st.webcam_status is SourceStatus.RUNNING:
            parts.append(f"Webcam {st.fps:.0f} fps")
        elif st.webcam_status is SourceStatus.FAILED:
            parts.append(f"Webcam failed: {st.webcam_error}")
        elif st.webcam_status is SourceStatus.STARTING:
            parts.append("Webcam starting…")
        else:
            parts.append("Webcam off")
        if st.phone_fresh:
            parts.append("Phone connected (driving the game)")
        elif st.phone_status is SourceStatus.FAILED:
            parts.append(f"Phone port: {st.phone_error}")
        if st.game_name and st.game_name != "Unknown game":
            parts.append(f"Game: {st.game_name}")
        elif any(getattr(o, "is_game_output", False) for o in self.app.engine._outputs):
            parts.append("Game output ready")
        if st.output_errors:
            parts.append("Output error: " + "; ".join(f"{k}: {v}" for k, v in st.output_errors.items()))
        self.status_var.set("  |  ".join(parts))

    def _update_choose(self, st: EngineState) -> None:
        if self._centre_done or self._stage != "choose":
            return
        if st.webcam_status is SourceStatus.FAILED:
            why = (st.webcam_error or "The camera could not be started").rstrip(".?! ")
            self.choose_cam_var.set(f"Not working: {why}")
            self.choose_preview.configure(image=self._blank_preview, text="No picture", compound="center")
        elif st.webcam_status is SourceStatus.RUNNING:
            self.choose_cam_var.set("Ready" if st.raw is not None else "Looking for your face…")
        elif st.webcam_status is SourceStatus.STARTING:
            self.choose_cam_var.set("Starting…")
        else:
            self.choose_cam_var.set("Off")
        if st.phone_fresh:
            self.choose_phone_var.set("Phone is sending now")
        else:
            self.choose_phone_var.set(f"{self.app.pc_name()} · {', '.join(local_ipv4_addresses()) or 'no network'}")

    def _update_phone_wait(self, st: EngineState) -> None:
        if self._centre_done or self._stage != "phone":
            return
        p = self.app.profile.phone
        self.phone_pc_name.set(self.app.pc_name())
        ips = local_ipv4_addresses()
        self.phone_pc_addr.set(f"{', '.join(ips) or 'no network'} : {p.track_port}")
        if st.phone_status is SourceStatus.FAILED:
            self.phone_title.set("Phone port busy")
            self.phone_wait_status.set(f"Phone port: {st.phone_error}")
            self.phone_wait_label.configure(foreground=ERR)
            return
        stats = getattr(self.app.engine.source(SourceKind.PHONE), "stats", None)
        s = stats.snapshot() if stats is not None else None
        if s is not None and (s.packets or s.discoveries) and self.phone_fw.winfo_ismapped():
            self.phone_fw.pack_forget()   # the phone got through, whatever the rule check said
        if st.phone_fresh:
            self.phone_title.set("Connected")
            who = f"{s.last_sender[0]} · " if s is not None and s.last_sender else ""
            self.phone_wait_status.set(f"{who}{st.fps:.0f} fps")
            self.phone_wait_label.configure(foreground=OK)
            self.phone_continue.configure(text="Continue", style="Accent.TButton")
            now = time.monotonic()
            if self._phone_fresh_since is None:
                self._phone_fresh_since = now
            elif now - self._phone_fresh_since > 1.2:   # a short look at "connected" before the tabs slide in
                self._skip_centre()
            return
        self._phone_fresh_since = None
        self.phone_continue.configure(text="Skip", style="TButton")
        self.phone_title.set("Waiting for the phone")
        self.phone_wait_label.configure(foreground=MUTED)
        if s is not None and s.discoveries and not s.packets:
            self.phone_wait_status.set("Found this PC: tap Connect on the phone")
        elif s is not None and s.packets:
            self.phone_wait_status.set("The phone stopped sending")
        else:
            self.phone_wait_status.set("" if self.app.discovery is not None else "Discovery off: type the IP on the phone")

    def _draw_phone_art(self, c: tk.Canvas, now: float, connected: bool, bg: str) -> None:
        """A phone sending Wi-Fi waves to a screen; the waves travel while waiting, stay lit once connected."""
        w, h = int(c.cget("width")), int(c.cget("height"))
        c.delete("all")
        fg = "#cfd3d8"
        px, py = w * 0.18, h / 2
        c.create_rectangle(px - 18, py - 34, px + 18, py + 34, outline=fg, width=3)
        c.create_oval(px - 3, py + 24, px + 3, py + 30, fill=fg, outline="")
        sx, sy = w * 0.82, h / 2
        c.create_rectangle(sx - 34, sy - 24, sx + 34, sy + 18, outline=fg, width=3)
        c.create_line(sx - 14, sy + 30, sx + 14, sy + 30, fill=fg, width=3)
        colour = "#2a2" if connected else ACCENT
        span = sx - px - 100
        for i in range(3):
            t = (i + 1) / 4.0 if connected else (anim.cycle(now, 1.6) + i / 3.0) % 1.0
            x, r = px + 30 + span * t, 10 + 14 * t
            col = colour if connected or t < 0.5 else "#8bd"   # waves fade as they travel while waiting
            c.create_arc(x - r, py - r, x + r, py + r, start=-45, extent=90, style="arc", outline=col, width=3)

    def _update_centre(self, st: EngineState) -> None:
        if self._centre_done or self._stage != "webcam":
            return
        if st.calibration is CalibrationPhase.COUNTDOWN:
            self.centre_btn.configure(text=f"Hold still… {st.calibration_seconds_left:.0f}", state="disabled")
            self.centre_msg.set("")
        elif st.calibration is CalibrationPhase.SAMPLING:
            self.centre_btn.configure(text="Measuring…", state="disabled")
        elif st.calibration is CalibrationPhase.FAILED:
            self.centre_btn.configure(text="Set centre", state="normal")
            self.centre_msg.set(st.calibration_message)
        elif st.calibration is CalibrationPhase.DONE and st.has_neutral:
            self._centre_done = True
            self._show_tabs()
            return
        else:
            self.centre_btn.configure(text="Set centre", state="normal")
        failed = st.webcam_status is SourceStatus.FAILED
        if failed:
            if not self.centre_problem_var.get().startswith("Restarting") or st.webcam_error != self._last_webcam_error:
                self.centre_problem_var.set(st.webcam_error or "The camera could not be started.")
            if not self.centre_problem.winfo_ismapped():
                self.centre_problem.pack(fill="x", pady=(10, 0))
        elif self.centre_problem.winfo_ismapped():
            self.centre_problem.pack_forget()
        self._last_webcam_error = st.webcam_error
        if failed:
            self.centre_hint.set("")
        elif st.raw is None:
            self.centre_hint.set("Looking for your face…" if st.webcam_status is SourceStatus.RUNNING else "Starting the webcam…")
        else:
            self.centre_hint.set("Face found")

    def _update_track(self, st: EngineState) -> None:
        if st.sweep is not None:
            self.sweep_btn.configure(text="Stop")
            self.sweep_var.set(f"{st.sweep.phase.label}: {st.sweep.phase.expect}  ({st.sweep_progress * 100:.0f} %)")
        else:
            self.sweep_btn.configure(text="Test")
            if self.sweep_var.get().endswith("%)"):
                self.sweep_var.set("Test done · reversed axis? Settings → Feel → Per axis → Invert")
        auto = "Automatic centre: adjusting to your resting pose…" if st.auto_centre_active else ""
        if st.eye_yaw_degrees or st.eye_pitch_degrees:
            auto = (auto + "  " if auto else "") + f"Eyes add {st.eye_yaw_degrees:+.0f}° yaw {st.eye_pitch_degrees:+.0f}° pitch"
        self.auto_var.set(auto)
        self.pause_btn.configure(text="Resume" if st.paused else "Pause",
                                 image=self.icons.get("play" if st.paused else "pause", 16) or "")
        eyes_on = st.screen_gaze_quality not in ("", "not calibrated")
        self.gaze_var.set(f"Eyes {st.screen_gaze_quality}" if eyes_on else "Eyes off")
        if eyes_on != bool(self.gaze_mini.winfo_ismapped()):
            if eyes_on:
                self.gaze_mini.pack(side="left", padx=8)
            else:
                self.gaze_mini.pack_forget()
        cal = st.screen_calibration
        if cal is not None and cal.phase in ("done", "failed"):
            self.screen_gaze_var.set(cal.message)
        elif cal is None:
            self.screen_gaze_var.set(st.screen_gaze_quality.capitalize())
        if self._last_api_requests != (self.app.server.requests if self.app.server else -1):
            self._last_api_requests = self.app.server.requests if self.app.server else -1
            self._refresh_api_msg()
        if self._last_tuning_label != self.app.tuning_label():
            self._last_tuning_label = self.app.tuning_label()
            self._load_tuning_fields()
            name = self.app.profile.name.lower()
            self.home_preset.set(next((k for k, pr in PRESETS.items() if pr.name.lower() in name), ""))
        if st.gaze_calibration_progress >= 0:
            self.eye_msg.set(f"Calibrating head-turn compensation: look at the screen centre and slowly turn your head left and right… {st.gaze_calibration_progress * 100:.0f} %")
        elif st.gaze_calibration_message and self.eye_msg.get().startswith("Calibrating"):
            self.eye_msg.set(st.gaze_calibration_message)
            self.eye_comp.set(self.app.profile.eye_assist.head_compensation)
        self.live_vars["raw"].set(_fmt(st.raw))
        self.live_vars["calibrated"].set(_fmt(st.calibrated) if st.has_neutral else "set the centre first")
        self.live_vars["output"].set(_fmt(st.output))
        state = {TrackingState.TRACKING: "Tracking", TrackingState.HOLDING: "Face lost",
                 TrackingState.RETURNING: "Face lost", TrackingState.NEUTRAL: "Looking for you…"}[st.tracking]
        if st.paused:
            state = "Paused"
        elif st.phone_fresh:
            state = "Tracking · phone"
        elif self.app.profile.source is SourceKind.PHONE:
            state = "Waiting for the phone…"
        if st.calibration in (CalibrationPhase.COUNTDOWN, CalibrationPhase.SAMPLING):
            state = f"Calibrating… {st.calibration_seconds_left:.0f}" if st.calibration is CalibrationPhase.COUNTDOWN else "Measuring…"
        elif st.calibration is CalibrationPhase.FAILED and st.calibration_message:
            state += f" — {st.calibration_message}"
        self.track_state.set(state)
        self.game_var.set(st.game_name if st.game_name and st.game_name != "Unknown game" else "No game")
        if self.app.engine.source(SourceKind.WEBCAM) is None and self.preview_label.winfo_ismapped():
            self.preview_label.place_forget()   # the phone is the camera: no picture to show

    def _update_connect(self, st: EngineState) -> None:
        ips = ", ".join(local_ipv4_addresses()) or "no network"
        p = self.app.profile.phone
        self.pc_name_var.set(self.app.pc_name())
        disc = "" if self.app.discovery is not None else f" · discovery {self.app.discovery_error or 'off'}"
        self.pc_var.set(f"{ips} : {p.track_port}{disc}")
        phone = self.app.engine.source(SourceKind.PHONE)
        stats = getattr(phone, "stats", None)
        if stats is None or st.phone_status is not SourceStatus.RUNNING:
            self.phone_var.set(st.phone_error or "Phone receiver off")
            return
        s = stats.snapshot()
        if s.packets == 0:
            self.phone_var.set("No phone yet")
        else:
            who = s.last_sender[0] if s.last_sender else "?"
            state = "connected" if st.phone_fresh else "stopped"
            lost = f" · {s.lost} lost" if s.lost else ""
            self.phone_var.set(f"Phone {who} {state} · {s.packets} packets{lost}")

    def _update_checks(self) -> None:
        import time as _t
        if _t.monotonic() - self._last_check_time < 1.0:
            return
        self._last_check_time = _t.monotonic()
        report = self.app.self_check()
        self.check_headline.set(report.headline)
        look = {CheckResult.PASS: ("circle-check", OK), CheckResult.WARN: ("triangle-alert", WARN),
                CheckResult.FAIL: ("circle-x", ERR), CheckResult.SKIP: ("circle-minus", MUTED)}
        for i, item in enumerate(report.items):
            if item.id not in self._check_widgets:
                mark = ttk.Label(self.check_rows)
                title = ttk.Label(self.check_rows, width=17, anchor="w")
                detail = ttk.Label(self.check_rows, wraplength=360, justify="left", anchor="w", style="Small.TLabel")
                fix = ttk.Button(self.check_rows)
                mark.grid(row=i, column=0, sticky="nw", padx=(0, 10), pady=7)
                title.grid(row=i, column=1, sticky="nw", pady=7)
                detail.grid(row=i, column=2, sticky="w", pady=7)
                self._check_widgets[item.id] = (mark, title, detail, fix)
            mark, title, detail, fix = self._check_widgets[item.id]
            if item.result is CheckResult.SKIP:   # not in use (e.g. the webcam rows in phone mode): no row
                for w in (mark, title, detail, fix):
                    w.grid_remove()
                continue
            for w in (mark, title, detail):
                w.grid()
            icon, colour = look[item.result]
            img = self.icons.get(icon, 18, colour)
            mark.configure(image=img or "", text="" if img else {OK: "✓", WARN: "!", ERR: "✗"}.get(colour, "–"), foreground=colour)
            title.configure(text=item.title, foreground=MUTED if item.result is CheckResult.SKIP else TEXT)
            detail.configure(text=item.detail, foreground=colour if item.result in (CheckResult.WARN, CheckResult.FAIL) else MUTED)
            if item.fix:
                fix.configure(text=item.fix_label or "Fix", command=lambda a=item.fix: self._run_fix(a))
                fix.grid(row=i, column=3, sticky="ne", padx=(8, 0), pady=4)
            else:
                fix.grid_forget()

    def _update_diagnostics(self, st: EngineState) -> None:
        self._update_checks()
        caps = self.app.engine.capabilities()
        lines = [
            f"source={st.active_source.value if st.active_source else '-'} webcam={st.webcam_status.value} phone={st.phone_status.value} fresh={st.phone_fresh}",
            f"frames={st.frames} fps={st.fps:.0f} latency={st.latency_ms:.1f} ms packets_out={st.packets_out} tracking={st.tracking.value}",
            f"centre={'set' if st.has_neutral else 'not set'} calibration={st.calibration.value} {st.calibration_message}",
            f"capabilities=0x{caps:02x} game_output={'yes' if caps & protocol.CAP_GAME_OUTPUT else 'no'} game={st.game_name or '-'} ({st.game_id}) tuning={self.app.profile.name}",
            f"eye_yaw={st.eye_yaw_degrees:+.1f} auto_centre={'on' if self.app.profile.auto_centre.enabled else 'off'}{' (adjusting)' if st.auto_centre_active else ''} hotkey={self.app.profile.hotkeys.recenter_key if self.app.profile.hotkeys.enabled else 'off'} fired={self.app.hotkeys.fired}",
            f"platform={sys.platform} python={sys.version.split()[0]}",
        ]
        self.diag_var.set("\n".join(lines))
        if not self.output_notes.get():
            self.output_notes.set("\n".join(self.app.output_notes))

    def _preview(self) -> None:
        try:
            src = self.app.engine.source(SourceKind.WEBCAM)
            frame = src.preview() if src is not None and hasattr(src, "preview") else None
            if frame is not None:
                import cv2
                small = cv2.resize(frame, (176, 132) if self._centre_done else (288, 216) if self._stage == "choose" else (320, 240))
                small = cv2.flip(small, 1)  # mirror for a natural feel; tracking uses the unflipped frame
                ok, ppm = cv2.imencode(".ppm", small)
                if ok:
                    self._preview_image = tk.PhotoImage(data=ppm.tobytes())
                    target = {"choose": self.choose_preview, "webcam": self.centre_preview}.get(self._stage, self.preview_label)
                    if self._centre_done:
                        target = self.preview_label
                        if not target.winfo_ismapped():
                            target.place(relx=1.0, rely=1.0, x=-10, y=-10, anchor="se")
                    target.configure(image=self._preview_image, text="", width=small.shape[1], height=small.shape[0])
        except Exception:
            pass
        self.root.after(PREVIEW_MS, self._preview)

    def run(self) -> int:
        self.app.start()
        if self.app.profile.source is not SourceKind.WEBCAM:
            self._back_to_choice()   # the chooser shows the webcam preview, so the webcam runs until the phone is picked
        self.root.mainloop()
        return 0


def run_gui(profile: TrackingProfile, profile_path: Optional[str] = None, steam=None) -> int:
    import logging
    import traceback
    try:
        app = App(profile, profile_path, log=logging.getLogger("headtrack").info)
        return HeadTrackWindow(app, steam).run()
    except Exception:
        text = traceback.format_exc()
        logging.getLogger("headtrack").error(text)
        try:
            from tkinter import messagebox
            messagebox.showerror("HeadTrack PC", f"HeadTrack PC could not start:\n\n{text}")
        except Exception:
            print(text, file=sys.stderr)
        return 1
