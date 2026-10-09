"""Tkinter window: the Android app's three steps on a PC.

  1. Set your centre (every launch)  2. Track: Recenter / Recalibrate, live values, preview
  3. Connect: how the phone finds this PC   4. Advanced: outputs, camera, tuning, diagnostics

The window never touches the engine thread directly: it reads `engine.state` on a 50 ms timer
and sends commands (calibrate, recenter, profile updates) that the engine executes itself.
"""
from __future__ import annotations

import sys
import tkinter as tk
from dataclasses import replace
from tkinter import ttk
from typing import Dict, Optional

import math

from . import __version__
from . import protocol
from . import sim
from .app import App
from .autocentre import AutoCentreSettings
from .engine import CalibrationPhase, EngineState
from .faceloss import TrackingState
from .filters import FilterType
from .gaze import EyeAssistSettings, GazeSource
from .hotkeys import HotkeySettings, parse_key
from .inputs.base import SourceStatus
from .mapping import AxisSettings, ResponseCurve
from .net.discovery import local_ipv4_addresses
from .pose import Axis, HeadPose
from .profile import FreetrackInterface, SourceKind, TrackingProfile
from .profiles import PRESETS
from .selfcheck import CheckResult

POLL_MS = 50
PREVIEW_MS = 66


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
        self.root.minsize(640, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._preview_image = None
        self._last_tuning_label = ""
        self._last_check_time = 0.0
        self._centre_done = False
        self._last_state: Optional[EngineState] = None
        self._axis_vars: Dict[str, Dict[str, tk.Variable]] = {}
        self._build()
        self.root.after(POLL_MS, self._poll)
        self.root.after(PREVIEW_MS, self._preview)
        if self.steam is not None:
            self.root.after(100, self._steam_callbacks)

    def _steam_callbacks(self) -> None:
        self.steam.run_callbacks()
        self.root.after(100, self._steam_callbacks)

    # --- layout -------------------------------------------------------------------------------
    def _build(self) -> None:
        root = self.root
        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(root, textvariable=self.status_var, anchor="w", padding=(8, 4)).pack(fill="x")

        self.container = ttk.Frame(root)
        self.container.pack(fill="both", expand=True)
        self.centre_frame = self._build_centre(self.container)
        self.tabs = ttk.Notebook(self.container)
        self.track_tab = self._build_track(self.tabs)
        self.connect_tab = self._build_connect(self.tabs)
        self.advanced_tab = self._build_advanced(self.tabs)
        self.tabs.add(self.track_tab, text="Track")
        self.tabs.add(self.connect_tab, text="Connect")
        self.tabs.add(self.advanced_tab, text="Advanced")
        self.centre_frame.pack(fill="both", expand=True)

    def _build_centre(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=24)
        ttk.Label(f, text="Set your centre", font=("", 18, "bold")).pack(pady=(10, 4))
        ttk.Label(f, wraplength=560, justify="center", text=(
            "Sit as you play, look straight at the screen and hold still. The centre is set again every "
            "launch because seat, camera and posture change between sessions.")).pack(pady=(0, 12))
        self.centre_preview = tk.Label(f, bg="#222", width=48, height=12)
        self.centre_preview.pack(pady=4)
        self.centre_msg = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.centre_msg, foreground="#a33", wraplength=560).pack(pady=4)
        row = ttk.Frame(f)
        row.pack(pady=8)
        self.centre_btn = ttk.Button(row, text="Calibrate centre (3 s)", command=self.app.calibrate)
        self.centre_btn.pack(side="left", padx=6)
        ttk.Button(row, text="Use instant centre", command=self.app.recenter).pack(side="left", padx=6)
        self.centre_skip = ttk.Button(row, text="Skip: the phone is tracking", command=self._skip_centre)
        self.centre_hint = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.centre_hint, foreground="#666", wraplength=560, justify="center").pack(pady=4)
        return f

    def _build_track(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=12)
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Button(top, text="Recenter (instant)", command=self.app.recenter).pack(side="left", padx=4)
        ttk.Button(top, text="Recalibrate (3 s)", command=self.app.calibrate).pack(side="left", padx=4)
        self.track_state = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.track_state).pack(side="left", padx=12)
        self.preview_label = tk.Label(f, bg="#222", width=48, height=14)
        self.preview_label.pack(pady=8)
        grid = ttk.Frame(f)
        grid.pack(fill="x")
        self.live_vars = {k: tk.StringVar(value="—") for k in ("raw", "calibrated", "output")}
        for i, (k, label) in enumerate((("raw", "Raw (camera)"), ("calibrated", "Centred"), ("output", "Sent to game"))):
            ttk.Label(grid, text=label, width=14).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Label(grid, textvariable=self.live_vars[k], font=("Courier", 10)).grid(row=i, column=1, sticky="w")
        self.game_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.game_var, foreground="#262").pack(anchor="w", pady=(8, 0))
        self.auto_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.auto_var, foreground="#666").pack(anchor="w")
        # Direction check: a cockpit that moves like a driving game's camera, driven by the pose being sent.
        box = ttk.LabelFrame(f, text="Direction check (what the game should do)", padding=6)
        box.pack(fill="x", pady=(8, 0))
        self.cockpit = tk.Canvas(box, width=360, height=180, bg="#9ec5e8", highlightthickness=0)
        self.cockpit.pack()
        self.cockpit_words = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.cockpit_words).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        self.sweep_btn = ttk.Button(row, text="Sweep each axis (25 s)", command=self._toggle_sweep)
        self.sweep_btn.pack(side="left")
        self.sweep_var = tk.StringVar(value="Sends yaw right/left, pitch up/down, roll right/left one at a time through the real pipeline: watch the game follow. Reversed axis → Invert it in Advanced → Tuning.")
        ttk.Label(row, textvariable=self.sweep_var, wraplength=420, justify="left").pack(side="left", padx=8)
        return f

    def _toggle_sweep(self) -> None:
        if self.app.engine.sweeping:
            self.app.engine.stop_sweep()
        else:
            self.app.engine.start_sweep()

    def _draw_cockpit(self, st: EngineState) -> None:
        cam = sim.camera(st.output)
        c = self.cockpit
        w, h = 360, 180
        c.delete("all")
        cx, cy = w / 2 + cam.pan_x * w, h / 2 + cam.pan_y * h
        ang = math.radians(cam.roll_degrees)
        cos_a, sin_a = math.cos(ang), math.sin(ang)

        def rot(x, y):
            return cx + (x * cos_a - y * sin_a) * cam.zoom, cy + (x * sin_a + y * cos_a) * cam.zoom
        # far scene: sky/ground split by the horizon, a road converging to the vanishing point
        far = [rot(-700, 0), rot(700, 0), rot(700, 600), rot(-700, 600)]
        c.create_polygon(*[v for p in far for v in p], fill="#5b8c3a", outline="")
        c.create_polygon(*[v for p in [rot(-40, 0), rot(40, 0), rot(360, 600), rot(-360, 600)] for v in p], fill="#555", outline="")
        c.create_line(*rot(0, 0), *rot(0, 600), fill="#eee", dash=(8, 8), width=2)
        for x in (-220, -120, 120, 220):
            c.create_rectangle(*rot(x - 10, -60), *rot(x + 10, 0), fill="#8a6", outline="")
        # near cockpit (dashboard, pillars, mirrors) moves with parallax and does not roll with the world
        px, py = cam.parallax_x * w, cam.parallax_y * h
        c.create_rectangle(0 + px, h * 0.72 + py, w + px, h + py, fill="#2b2b2b", outline="")
        c.create_rectangle(-20 + px, 0 + py, 30 + px, h + py, fill="#1e1e1e", outline="")
        c.create_rectangle(w - 30 + px, 0 + py, w + 20 + px, h + py, fill="#1e1e1e", outline="")
        c.create_rectangle(w * 0.42 + px, 6 + py, w * 0.58 + px, 26 + py, fill="#111", outline="#888")
        c.create_oval(w * 0.3 + px, h * 0.6 + py, w * 0.7 + px, h * 1.3 + py, outline="#777", width=6)
        self.cockpit_words.set("Looking at: " + cam.words)

    def _build_connect(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=12)
        ttk.Label(f, text="Use the phone instead of the webcam (or alongside it)", font=("", 11, "bold")).pack(anchor="w")
        ttk.Label(f, wraplength=580, justify="left", text=(
            "1. Put the phone and this PC on the same Wi-Fi.\n"
            "2. In the HeadTrack Android app open the Connect tab and tap \"Find PC on this network\": this PC "
            "answers with its name, so there is no IP address to type.\n"
            "3. Tap the PC, then Connect. The phone's packets replace the webcam while they arrive; the webcam "
            "takes over again when the phone stops.\n"
            "No opentrack is needed: this program feeds the game directly.")).pack(anchor="w", pady=6)
        self.pc_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.pc_var, font=("Courier", 10)).pack(anchor="w", pady=4)
        self.phone_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.phone_var, wraplength=580, justify="left").pack(anchor="w", pady=4)
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=6)
        ttk.Label(row, text="PC name shown on the phone:").pack(side="left")
        self.name_var = tk.StringVar(value=self.app.profile.phone.pc_name)
        e = ttk.Entry(row, textvariable=self.name_var, width=24)
        e.pack(side="left", padx=4)
        ttk.Button(row, text="Apply", command=self._apply_name).pack(side="left")
        self.discovery_var = tk.BooleanVar(value=self.app.profile.phone.discovery_enabled)
        ttk.Checkbutton(f, text="Answer the phone's search (discovery, UDP port 4244)", variable=self.discovery_var,
                        command=self._apply_name).pack(anchor="w")
        ttk.Label(f, foreground="#666", wraplength=580, justify="left", text=(
            "Windows Firewall: allow HeadTrack PC on private networks when asked, or add inbound UDP rules for "
            "ports 4242 and 4244. Without that the phone cannot find or reach this PC.")).pack(anchor="w", pady=6)
        return f

    def _build_advanced(self, parent) -> ttk.Frame:
        outer = ttk.Frame(parent)
        canvas = tk.Canvas(outer, highlightthickness=0)
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        f = ttk.Frame(canvas, padding=12)
        f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=f, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        p = self.app.profile

        # Output
        box = ttk.LabelFrame(f, text="Game output", padding=8)
        box.pack(fill="x", pady=4)
        self.ft_var = tk.BooleanVar(value=p.output.freetrack_enabled)
        ttk.Checkbutton(box, text="freetrack 2.0 / TrackIR emulation (what opentrack's 'freetrack 2.0 Enhanced' does)",
                        variable=self.ft_var, command=self._apply_output).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w")
        ttk.Label(row, text="Interface:").pack(side="left")
        self.ft_iface = tk.StringVar(value=p.output.freetrack_interface.value)
        for v, label in (("both", "both (default)"), ("npclient", "TrackIR only"), ("freetrack", "FreeTrack only")):
            ttk.Radiobutton(row, text=label, value=v, variable=self.ft_iface, command=self._apply_output).pack(side="left", padx=4)
        self.udp_var = tk.BooleanVar(value=p.output.udp_enabled)
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=2)
        ttk.Checkbutton(row, text="Also send UDP to opentrack at", variable=self.udp_var, command=self._apply_output).pack(side="left")
        self.udp_host = tk.StringVar(value=p.output.udp_host)
        self.udp_port = tk.StringVar(value=str(p.output.udp_port))
        ttk.Entry(row, textvariable=self.udp_host, width=16).pack(side="left", padx=2)
        ttk.Label(row, text=":").pack(side="left")
        ttk.Entry(row, textvariable=self.udp_port, width=6).pack(side="left", padx=2)
        ttk.Button(row, text="Apply", command=self._apply_output).pack(side="left", padx=4)
        self.output_notes = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.output_notes, wraplength=560, justify="left", foreground="#444").pack(anchor="w", pady=2)

        # Camera
        box = ttk.LabelFrame(f, text="Camera", padding=8)
        box.pack(fill="x", pady=4)
        row = ttk.Frame(box)
        row.pack(anchor="w")
        ttk.Label(row, text="Source:").pack(side="left")
        self.source_var = tk.StringVar(value=p.source.value)
        ttk.Radiobutton(row, text="Webcam (+ phone when it sends)", value="webcam", variable=self.source_var, command=self._apply_camera).pack(side="left", padx=4)
        ttk.Radiobutton(row, text="Phone only", value="phone", variable=self.source_var, command=self._apply_camera).pack(side="left", padx=4)
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=2)
        ttk.Label(row, text="Webcam index:").pack(side="left")
        self.cam_index = tk.StringVar(value=str(p.camera.index))
        ttk.Spinbox(row, from_=0, to=9, textvariable=self.cam_index, width=4).pack(side="left", padx=4)
        self.mirror_var = tk.BooleanVar(value=p.camera.mirrored)
        ttk.Checkbutton(row, text="Camera image is mirrored (flip yaw/roll/x)", variable=self.mirror_var).pack(side="left", padx=8)
        ttk.Button(row, text="Apply", command=self._apply_camera).pack(side="left", padx=4)

        # Tuning
        box = ttk.LabelFrame(f, text="Tuning (same meaning as the Android app)", padding=8)
        box.pack(fill="x", pady=4)
        self.tuning_for = tk.StringVar(value=self.app.tuning_label())
        ttk.Label(box, textvariable=self.tuning_for, wraplength=560, justify="left", foreground="#262").grid(row=10, column=0, columnspan=7, sticky="w", pady=(0, 4))
        prow = ttk.Frame(box)
        prow.grid(row=11, column=0, columnspan=7, sticky="w", pady=(0, 6))
        ttk.Label(prow, text="Preset:").pack(side="left")
        for key, preset in PRESETS.items():
            ttk.Button(prow, text=preset.name, command=lambda k=key: self._apply_preset(k)).pack(side="left", padx=3)
        ttk.Label(prow, text="Games switch to their own tuning when they connect (driving / flight preset first).", foreground="#666", wraplength=330, justify="left").pack(side="left", padx=8)
        hdr = ("Axis", "On", "Sensitivity", "Dead zone", "Max", "Curve", "Invert")
        for c, h in enumerate(hdr):
            ttk.Label(box, text=h, font=("", 9, "bold")).grid(row=12, column=c, padx=4)
        for r, axis in enumerate(Axis, start=13):
            s: AxisSettings = p.mapping.get(axis)
            vars_ = {
                "enabled": tk.BooleanVar(value=s.enabled), "sensitivity": tk.StringVar(value=f"{s.sensitivity:g}"),
                "dead_zone": tk.StringVar(value=f"{s.dead_zone:g}"), "max_output": tk.StringVar(value=f"{s.max_output:g}"),
                "curve": tk.StringVar(value=s.curve.name), "inverted": tk.BooleanVar(value=s.inverted),
            }
            self._axis_vars[axis.name] = vars_
            ttk.Label(box, text=f"{axis.label} ({axis.unit})").grid(row=r, column=0, sticky="w", padx=4)
            ttk.Checkbutton(box, variable=vars_["enabled"]).grid(row=r, column=1)
            ttk.Entry(box, textvariable=vars_["sensitivity"], width=6).grid(row=r, column=2)
            ttk.Entry(box, textvariable=vars_["dead_zone"], width=6).grid(row=r, column=3)
            ttk.Entry(box, textvariable=vars_["max_output"], width=6).grid(row=r, column=4)
            ttk.Combobox(box, textvariable=vars_["curve"], values=[c.name for c in ResponseCurve], width=9, state="readonly").grid(row=r, column=5)
            ttk.Checkbutton(box, variable=vars_["inverted"]).grid(row=r, column=6)
        row = ttk.Frame(box)
        row.grid(row=20, column=0, columnspan=7, sticky="w", pady=4)
        ttk.Label(row, text="Smoothing:").pack(side="left")
        self.smooth_type = tk.StringVar(value=p.smoothing.type.name)
        ttk.Combobox(row, textvariable=self.smooth_type, values=[t.name for t in FilterType], width=12, state="readonly").pack(side="left", padx=4)
        ttk.Label(row, text="strength 0..1:").pack(side="left")
        self.smooth_strength = tk.StringVar(value=f"{p.smoothing.strength:g}")
        ttk.Entry(row, textvariable=self.smooth_strength, width=6).pack(side="left", padx=4)
        ttk.Button(row, text="Apply tuning", command=self._apply_tuning).pack(side="left", padx=8)
        ttk.Button(row, text="Driving defaults", command=self._reset_tuning).pack(side="left")
        self.tuning_msg = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.tuning_msg, foreground="#a33").grid(row=21, column=0, columnspan=7, sticky="w")

        # Automatic centre + hotkeys
        box = ttk.LabelFrame(f, text="Recenter", padding=8)
        box.pack(fill="x", pady=4)
        self.auto_centre_var = tk.BooleanVar(value=p.auto_centre.enabled)
        ttk.Checkbutton(box, text="Automatic centre: when you sit still for 2 s within 12° of the centre, the centre drifts to your resting pose (no jump, never while looking aside)",
                        variable=self.auto_centre_var, command=self._apply_recenter).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=2)
        self.hotkey_var = tk.BooleanVar(value=p.hotkeys.enabled)
        ttk.Checkbutton(row, text="Recenter hotkey (works inside the game):", variable=self.hotkey_var, command=self._apply_recenter).pack(side="left")
        self.hotkey_key = tk.StringVar(value=p.hotkeys.recenter_key)
        ttk.Entry(row, textvariable=self.hotkey_key, width=10).pack(side="left", padx=4)
        ttk.Label(row, text="wheel/joystick").pack(side="left", padx=(8, 2))
        self.joy_id = tk.StringVar(value=str(p.hotkeys.joystick_id))
        ttk.Spinbox(row, from_=-1, to=15, textvariable=self.joy_id, width=4).pack(side="left")
        ttk.Label(row, text="button").pack(side="left", padx=(6, 2))
        self.joy_btn = tk.StringVar(value=str(p.hotkeys.joystick_button))
        ttk.Spinbox(row, from_=-1, to=31, textvariable=self.joy_btn, width=4).pack(side="left")
        ttk.Button(row, text="Apply", command=self._apply_recenter).pack(side="left", padx=6)
        self.hotkey_msg = tk.StringVar(value="Keys: F1–F24, A–Z, 0–9, Space, Home, End, Insert, Pause, Numpad0/5. Joystick -1 = none; buttons count from 0.")
        ttk.Label(box, textvariable=self.hotkey_msg, foreground="#666", wraplength=560, justify="left").pack(anchor="w")

        # Eye-assisted look
        box = ttk.LabelFrame(f, text="Eye-assisted look (experimental)", padding=8)
        box.pack(fill="x", pady=4)
        ttk.Label(box, wraplength=560, justify="left", text=(
            "A sideways glance adds yaw on top of the head pose, so you can check a mirror without turning your head "
            "away from the screen. Horizontal only. Costs some CPU (eye model).")).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=2)
        self.eye_var = tk.BooleanVar(value=p.eye_assist.enabled)
        ttk.Checkbutton(row, text="On", variable=self.eye_var, command=self._apply_eye).pack(side="left")
        ttk.Label(row, text="source").pack(side="left", padx=(8, 2))
        self.eye_source = tk.StringVar(value=p.eye_assist.source.name)
        ttk.Combobox(row, textvariable=self.eye_source, values=[g.name for g in GazeSource], width=7, state="readonly").pack(side="left")
        ttk.Label(row, text="dead zone").pack(side="left", padx=(8, 2))
        self.eye_dead = tk.StringVar(value=f"{p.eye_assist.dead_zone:g}")
        ttk.Entry(row, textvariable=self.eye_dead, width=5).pack(side="left")
        ttk.Label(row, text="gain °").pack(side="left", padx=(8, 2))
        self.eye_gain = tk.StringVar(value=f"{p.eye_assist.gain_degrees:g}")
        ttk.Entry(row, textvariable=self.eye_gain, width=5).pack(side="left")
        ttk.Label(row, text="max °").pack(side="left", padx=(8, 2))
        self.eye_max = tk.StringVar(value=f"{p.eye_assist.max_degrees:g}")
        ttk.Entry(row, textvariable=self.eye_max, width=5).pack(side="left")
        ttk.Button(row, text="Apply", command=self._apply_eye).pack(side="left", padx=6)
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=2)
        self.eye_comp = tk.BooleanVar(value=p.eye_assist.head_compensation)
        ttk.Checkbutton(row, text="Head-turn compensation (one screen: the eyes counter-rotate while the head turns)", variable=self.eye_comp, command=self._apply_eye).pack(side="left")
        ttk.Button(row, text="Calibrate (6 s)", command=self.app.engine.start_gaze_calibration).pack(side="left", padx=6)
        self.eye_msg = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.eye_msg, wraplength=560, justify="left").pack(anchor="w")

        # Diagnostics
        box = ttk.LabelFrame(f, text="Diagnostics", padding=8)
        box.pack(fill="x", pady=4)
        self.check_headline = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.check_headline, font=("", 10, "bold")).pack(anchor="w")
        self.check_rows = ttk.Frame(box)
        self.check_rows.pack(fill="x")
        self._check_widgets: Dict[str, tuple] = {}
        self.fix_msg = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.fix_msg, foreground="#262", wraplength=560, justify="left").pack(anchor="w")
        self.diag_var = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.diag_var, font=("Courier", 9), justify="left").pack(anchor="w", pady=(6, 0))
        ttk.Button(box, text="Copy report", command=self._copy_report).pack(anchor="w", pady=2)
        steam_line = f" {self.steam.status.message}." if self.steam is not None else ""
        ttk.Label(f, foreground="#666", wraplength=560, justify="left", text=(
            f"HeadTrack PC {__version__}.{steam_line} Same tracking as the Android app (MediaPipe Face Landmarker, "
            "Apache 2.0). Game output re-implements opentrack's freetrack protocol and ships its client DLLs "
            "(opentrack, ISC licence). Everything stays on this PC and your LAN.")).pack(anchor="w", pady=8)
        return outer

    # --- actions ----------------------------------------------------------------------------------
    def _skip_centre(self) -> None:
        self._centre_done = True
        self._show_tabs()

    def _show_tabs(self) -> None:
        if self.centre_frame.winfo_ismapped():
            self.centre_frame.pack_forget()
            self.tabs.pack(fill="both", expand=True)

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
        try:
            idx = int(self.cam_index.get())
        except ValueError:
            idx = 0
        self.app.update_profile(replace(p, source=SourceKind(self.source_var.get()),
                                        camera=replace(p.camera, index=idx, mirrored=bool(self.mirror_var.get()))))

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
        self.tuning_for.set(self.app.tuning_label())

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
        self.app.update_profile(replace(p, auto_centre=replace(p.auto_centre, enabled=bool(self.auto_centre_var.get())),
                                        hotkeys=HotkeySettings(enabled=bool(self.hotkey_var.get()), recenter_key=key or "F12",
                                                               joystick_id=jid, joystick_button=jbtn)))
        self.hotkey_msg.set("Applied." + (f" {self.app.hotkeys.error}" if self.app.hotkeys.error else ""))

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

    # --- polling ----------------------------------------------------------------------------------
    def _poll(self) -> None:
        st = self.app.engine.state
        self._last_state = st
        self._update_status(st)
        self._update_centre(st)
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

    def _update_centre(self, st: EngineState) -> None:
        if self._centre_done:
            return
        if st.calibration is CalibrationPhase.COUNTDOWN:
            self.centre_btn.configure(text=f"Hold still… {st.calibration_seconds_left:.0f}", state="disabled")
            self.centre_msg.set("")
        elif st.calibration is CalibrationPhase.SAMPLING:
            self.centre_btn.configure(text="Measuring…", state="disabled")
        elif st.calibration is CalibrationPhase.FAILED:
            self.centre_btn.configure(text="Calibrate centre (3 s)", state="normal")
            self.centre_msg.set(st.calibration_message)
        elif st.calibration is CalibrationPhase.DONE and st.has_neutral:
            self._centre_done = True
            self._show_tabs()
            return
        else:
            self.centre_btn.configure(text="Calibrate centre (3 s)", state="normal")
        if st.webcam_status is SourceStatus.FAILED:
            self.centre_hint.set(st.webcam_error or "")
        elif st.raw is None:
            self.centre_hint.set("Waiting for a face in the webcam…" if st.webcam_status is SourceStatus.RUNNING else "Starting the webcam…")
        else:
            self.centre_hint.set("Face tracked. Press Calibrate centre when you sit as you play.")
        if st.phone_fresh or self.app.profile.source is SourceKind.PHONE:
            if not self.centre_skip.winfo_ismapped():
                self.centre_skip.pack(side="left", padx=6)
        elif self.centre_skip.winfo_ismapped():
            self.centre_skip.pack_forget()

    def _update_track(self, st: EngineState) -> None:
        self._draw_cockpit(st)
        if st.sweep is not None:
            self.sweep_btn.configure(text="Stop sweep")
            self.sweep_var.set(f"{st.sweep.phase.label}: {st.sweep.phase.expect}  ({st.sweep_progress * 100:.0f} %)")
        else:
            self.sweep_btn.configure(text="Sweep each axis (25 s)")
            if self.sweep_var.get().endswith("%)"):
                self.sweep_var.set("Sweep finished. Every direction right? Then you are set. Reversed axis → Invert it in Advanced → Tuning.")
        auto = "Automatic centre: adjusting to your resting pose…" if st.auto_centre_active else ""
        if st.eye_yaw_degrees:
            auto = (auto + "  " if auto else "") + f"Eye-assisted look adds {st.eye_yaw_degrees:+.0f}° yaw"
        self.auto_var.set(auto)
        if self._last_tuning_label != self.app.tuning_label():
            self._last_tuning_label = self.app.tuning_label()
            self._load_tuning_fields()
        if st.gaze_calibration_progress >= 0:
            self.eye_msg.set(f"Calibrating head-turn compensation: look at the screen centre and slowly turn your head left and right… {st.gaze_calibration_progress * 100:.0f} %")
        elif st.gaze_calibration_message and self.eye_msg.get().startswith("Calibrating"):
            self.eye_msg.set(st.gaze_calibration_message)
            self.eye_comp.set(self.app.profile.eye_assist.head_compensation)
        self.live_vars["raw"].set(_fmt(st.raw))
        self.live_vars["calibrated"].set(_fmt(st.calibrated) if st.has_neutral else "set the centre first")
        self.live_vars["output"].set(_fmt(st.output))
        state = {TrackingState.TRACKING: "Tracking", TrackingState.HOLDING: "Face lost: holding",
                 TrackingState.RETURNING: "Face lost: returning to centre", TrackingState.NEUTRAL: "No face: centre"}[st.tracking]
        if st.phone_fresh:
            state = "Phone is driving the game"
        if st.calibration in (CalibrationPhase.COUNTDOWN, CalibrationPhase.SAMPLING):
            state = f"Calibrating… {st.calibration_seconds_left:.0f}" if st.calibration is CalibrationPhase.COUNTDOWN else "Measuring…"
        elif st.calibration is CalibrationPhase.FAILED and st.calibration_message:
            state += f" — {st.calibration_message}"
        self.track_state.set(state)
        self.game_var.set(f"Game connected: {st.game_name}" if st.game_name and st.game_name != "Unknown game" else "")

    def _update_connect(self, st: EngineState) -> None:
        ips = ", ".join(local_ipv4_addresses()) or "no network"
        p = self.app.profile.phone
        disc = "on" if (self.app.discovery is not None) else (self.app.discovery_error or "off")
        self.pc_var.set(f"This PC: {self.app.pc_name()}   IP: {ips}   pose port {p.track_port}   discovery {disc}")
        phone = self.app.engine.source(SourceKind.PHONE)
        stats = getattr(phone, "stats", None)
        if stats is None or st.phone_status is not SourceStatus.RUNNING:
            self.phone_var.set(st.phone_error or "Phone receiver not running")
            return
        s = stats.snapshot()
        if s.packets == 0:
            self.phone_var.set("No packets from a phone yet.")
        else:
            who = f"{s.last_sender[0]}:{s.last_sender[1]}" if s.last_sender else "?"
            self.phone_var.set(f"Phone {who}: {s.packets} packets ({s.extended} extended, {s.lost} lost, {s.invalid} invalid), "
                               f"{s.pings} pings answered, {s.discoveries} searches answered. "
                               + ("Fresh: the phone drives the game." if st.phone_fresh else "Stale: the webcam drives the game."))

    def _update_checks(self) -> None:
        import time as _t
        if _t.monotonic() - self._last_check_time < 1.0:
            return
        self._last_check_time = _t.monotonic()
        report = self.app.self_check()
        self.check_headline.set(report.headline)
        colours = {CheckResult.PASS: "#262", CheckResult.WARN: "#a60", CheckResult.FAIL: "#a33", CheckResult.SKIP: "#888"}
        marks = {CheckResult.PASS: "✓", CheckResult.WARN: "!", CheckResult.FAIL: "✗", CheckResult.SKIP: "–"}
        for i, item in enumerate(report.items):
            if item.id not in self._check_widgets:
                mark = ttk.Label(self.check_rows, width=2)
                title = ttk.Label(self.check_rows, width=16, anchor="w")
                detail = ttk.Label(self.check_rows, wraplength=380, justify="left", anchor="w")
                fix = ttk.Button(self.check_rows, width=22)
                mark.grid(row=i, column=0, sticky="nw", padx=2, pady=1)
                title.grid(row=i, column=1, sticky="nw", pady=1)
                detail.grid(row=i, column=2, sticky="w", pady=1)
                self._check_widgets[item.id] = (mark, title, detail, fix)
            mark, title, detail, fix = self._check_widgets[item.id]
            mark.configure(text=marks[item.result], foreground=colours[item.result])
            title.configure(text=item.title)
            detail.configure(text=item.detail, foreground=colours[item.result])
            if item.fix:
                fix.configure(text=item.fix_label or "Fix", command=lambda a=item.fix: self._run_fix(a))
                fix.grid(row=i, column=3, sticky="ne", padx=4)
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
                small = cv2.resize(frame, (320, 240))
                small = cv2.flip(small, 1)  # mirror for a natural feel; tracking uses the unflipped frame
                ok, ppm = cv2.imencode(".ppm", small)
                if ok:
                    self._preview_image = tk.PhotoImage(data=ppm.tobytes())
                    target = self.preview_label if self._centre_done else self.centre_preview
                    target.configure(image=self._preview_image, width=320, height=240)
        except Exception:
            pass
        self.root.after(PREVIEW_MS, self._preview)

    def run(self) -> int:
        self.app.start()
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
