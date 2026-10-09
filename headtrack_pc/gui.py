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

from . import __version__
from . import protocol
from .app import App
from .engine import CalibrationPhase, EngineState
from .faceloss import TrackingState
from .filters import FilterType
from .inputs.base import SourceStatus
from .mapping import AxisSettings, ResponseCurve
from .net.discovery import local_ipv4_addresses
from .pose import Axis, HeadPose
from .profile import FreetrackInterface, SourceKind, TrackingProfile

POLL_MS = 50
PREVIEW_MS = 66


def _fmt(p: Optional[HeadPose]) -> str:
    if p is None:
        return "no face"
    return f"yaw {p.yaw:6.1f}°  pitch {p.pitch:6.1f}°  roll {p.roll:6.1f}°"


class HeadTrackWindow:
    def __init__(self, app: App):
        self.app = app
        self.root = tk.Tk()
        self.root.title(f"HeadTrack PC {__version__}")
        self.root.minsize(640, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._preview_image = None
        self._centre_done = False
        self._last_state: Optional[EngineState] = None
        self._axis_vars: Dict[str, Dict[str, tk.Variable]] = {}
        self._build()
        self.root.after(POLL_MS, self._poll)
        self.root.after(PREVIEW_MS, self._preview)

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
        return f

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
        hdr = ("Axis", "On", "Sensitivity", "Dead zone", "Max", "Curve", "Invert")
        for c, h in enumerate(hdr):
            ttk.Label(box, text=h, font=("", 9, "bold")).grid(row=0, column=c, padx=4)
        for r, axis in enumerate(Axis, start=1):
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
        row.grid(row=8, column=0, columnspan=7, sticky="w", pady=4)
        ttk.Label(row, text="Smoothing:").pack(side="left")
        self.smooth_type = tk.StringVar(value=p.smoothing.type.name)
        ttk.Combobox(row, textvariable=self.smooth_type, values=[t.name for t in FilterType], width=12, state="readonly").pack(side="left", padx=4)
        ttk.Label(row, text="strength 0..1:").pack(side="left")
        self.smooth_strength = tk.StringVar(value=f"{p.smoothing.strength:g}")
        ttk.Entry(row, textvariable=self.smooth_strength, width=6).pack(side="left", padx=4)
        ttk.Button(row, text="Apply tuning", command=self._apply_tuning).pack(side="left", padx=8)
        ttk.Button(row, text="Driving defaults", command=self._reset_tuning).pack(side="left")
        self.tuning_msg = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.tuning_msg, foreground="#a33").grid(row=9, column=0, columnspan=7, sticky="w")

        # Diagnostics
        box = ttk.LabelFrame(f, text="Diagnostics", padding=8)
        box.pack(fill="x", pady=4)
        self.diag_var = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.diag_var, font=("Courier", 9), justify="left").pack(anchor="w")
        ttk.Button(box, text="Copy report", command=self._copy_report).pack(anchor="w", pady=2)
        ttk.Label(f, foreground="#666", wraplength=560, justify="left", text=(
            f"HeadTrack PC {__version__}. Same tracking as the Android app (MediaPipe Face Landmarker, "
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

    def _reset_tuning(self) -> None:
        from .profile import DRIVING
        p = self.app.profile
        self.app.update_profile(replace(p, mapping=DRIVING.mapping, smoothing=DRIVING.smoothing))
        for axis in Axis:
            s = DRIVING.mapping.get(axis)
            v = self._axis_vars[axis.name]
            v["enabled"].set(s.enabled); v["sensitivity"].set(f"{s.sensitivity:g}"); v["dead_zone"].set(f"{s.dead_zone:g}")
            v["max_output"].set(f"{s.max_output:g}"); v["curve"].set(s.curve.name); v["inverted"].set(s.inverted)
        self.smooth_type.set(DRIVING.smoothing.type.name)
        self.smooth_strength.set(f"{DRIVING.smoothing.strength:g}")

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

    def _update_diagnostics(self, st: EngineState) -> None:
        caps = self.app.engine.capabilities()
        lines = [
            f"source={st.active_source.value if st.active_source else '-'} webcam={st.webcam_status.value} phone={st.phone_status.value} fresh={st.phone_fresh}",
            f"frames={st.frames} fps={st.fps:.0f} latency={st.latency_ms:.1f} ms packets_out={st.packets_out} tracking={st.tracking.value}",
            f"centre={'set' if st.has_neutral else 'not set'} calibration={st.calibration.value} {st.calibration_message}",
            f"capabilities=0x{caps:02x} game_output={'yes' if caps & protocol.CAP_GAME_OUTPUT else 'no'} game={st.game_name or '-'}",
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


def run_gui(profile: TrackingProfile, profile_path: Optional[str] = None) -> int:
    import logging
    import traceback
    try:
        app = App(profile, profile_path, log=logging.getLogger("headtrack").info)
        return HeadTrackWindow(app).run()
    except Exception:
        text = traceback.format_exc()
        logging.getLogger("headtrack").error(text)
        try:
            from tkinter import messagebox
            messagebox.showerror("HeadTrack PC", f"HeadTrack PC could not start:\n\n{text}")
        except Exception:
            print(text, file=sys.stderr)
        return 1
