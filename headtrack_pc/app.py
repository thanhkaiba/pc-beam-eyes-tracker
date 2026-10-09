"""Builds the engine, sources and outputs from a profile. Used by the GUI and the CLI."""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import Callable, List, Optional

import time

from . import fixes, selfcheck
from . import profile as prof
from .engine import EngineState, TrackingEngine
from .hotkeys import HotkeyPoller
from .inputs.base import SourceStatus
from .inputs.phone import PhoneSource
from .inputs.webcam import WebcamSource
from .net.discovery import DiscoveryResponder, default_pc_name
from .outputs.freetrack import FreetrackOutput, OutputUnavailable, find_libs_dir
from .outputs.mouse import MouseMode, MouseOutput
from .outputs.udp import UdpOutput
from .server import StateServer
from .profile import SourceKind, TrackingProfile
from .profiles import PRESETS, ProfileLibrary, category_of, with_tuning


class GameOutputAdapter:
    """Engine `Output` wrapper around FreetrackOutput (adds the name and the game-output flag)."""
    name = "freetrack"
    is_game_output = True

    def __init__(self, inner: FreetrackOutput):
        self.inner = inner

    @property
    def game_name(self) -> str:
        return self.inner.game_name

    @property
    def game_id(self) -> int:
        return self.inner.game_id

    def write(self, pose, raw=None, flags=0, timestamp_nanos=0):
        self.inner.write(pose, raw)

    def close(self):
        self.inner.close()


class UdpOutputAdapter:
    name = "udp"
    is_game_output = False
    game_name = ""

    def __init__(self, inner: UdpOutput):
        self.inner = inner

    def write(self, pose, raw=None, flags=0, timestamp_nanos=0):
        self.inner.write(pose, raw, flags, timestamp_nanos)

    def close(self):
        self.inner.close()


class App:
    """Owns the engine and (re)creates sources/outputs when the profile changes."""

    def __init__(self, profile: Optional[TrackingProfile] = None, profile_path: Optional[str] = None,
                 log: Callable[[str], None] = print, library: Optional[ProfileLibrary] = None,
                 hotkeys: Optional[HotkeyPoller] = None):
        self.profile_path = profile_path
        self.profile = profile if profile is not None else prof.load(profile_path)
        self.profile = replace(self.profile, neutral_pose=None)  # the centre is set every launch
        # The global (no-game) tuning; `profile` is what the engine runs (a game's tuning once one connects).
        self.base_profile = self.profile
        self.log = log
        self.engine = TrackingEngine(self.profile)
        self.discovery: Optional[DiscoveryResponder] = None
        self.output_notes: List[str] = []
        self.discovery_error: Optional[str] = None
        self.library = library or ProfileLibrary(os.path.join(os.path.dirname(profile_path), "games") if profile_path else None)
        self.active_game_id = 0
        self.active_game_name = ""
        self.hotkeys = hotkeys if hotkeys is not None else HotkeyPoller(
            self.profile.hotkeys, self.recenter, on_toggle=self.engine.toggle_pause, on_gaze_warp=self.gaze_warp)
        self.server: Optional[StateServer] = None
        self._mouse: Optional[MouseOutput] = None
        self._webcam_started_at = 0.0
        self.engine.add_listener(self._on_engine_state)
        self.engine.add_profile_listener(self._on_engine_profile)

    # --- lifecycle ---------------------------------------------------------------------------
    def start(self) -> None:
        self.engine.start()
        self.apply_outputs()
        self.apply_sources()
        self.apply_discovery()
        self.apply_api()
        if self.profile.hotkeys.enabled:
            self.hotkeys.start()
            if self.hotkeys.error:
                self.log(self.hotkeys.error)

    def stop(self) -> None:
        self.hotkeys.stop()
        if self.server is not None:
            self.server.stop()
            self.server = None
        if self.discovery is not None:
            self.discovery.close()
            self.discovery = None
        self.engine.stop()

    def pc_name(self) -> str:
        return self.profile.phone.pc_name.strip() or default_pc_name()

    # --- (re)configuration ----------------------------------------------------------------------
    def apply_outputs(self) -> None:
        self.engine.remove_outputs()
        self.output_notes = []
        out = self.profile.output
        if out.freetrack_enabled:
            if sys.platform != "win32":
                self.output_notes.append("Game output (freetrack/TrackIR) needs Windows; not active here.")
            else:
                try:
                    self.engine.add_output(GameOutputAdapter(FreetrackOutput(out.freetrack_interface.value)))
                    self.output_notes.append(f"Game output active (freetrack/TrackIR, {out.freetrack_interface.value}); DLLs in {find_libs_dir()}")
                except OutputUnavailable as e:
                    self.output_notes.append(f"Game output NOT active: {e}")
                except Exception as e:  # registry/mapping errors
                    self.output_notes.append(f"Game output failed: {e}")
        if out.udp_enabled:
            try:
                self.engine.add_output(UdpOutputAdapter(UdpOutput(out.udp_host, out.udp_port)))
                self.output_notes.append(f"UDP output to {out.udp_host}:{out.udp_port} (opentrack format)")
            except OSError as e:
                self.output_notes.append(f"UDP output failed: {e}")
        self._mouse = None
        if self.profile.mouse.mode is not MouseMode.OFF:
            try:
                self._mouse = MouseOutput(self.profile.mouse)
                self.engine.add_output(self._mouse)
                self.output_notes.append(f"Mouse output: {self.profile.mouse.mode.value}")
            except RuntimeError as e:
                self.output_notes.append(f"Mouse output not active: {e}")
        for n in self.output_notes:
            self.log(n)

    def apply_sources(self) -> None:
        p = self.profile
        if p.source is SourceKind.WEBCAM:
            self._webcam_started_at = time.monotonic()
            self.engine.set_source(SourceKind.WEBCAM, WebcamSource(p.camera, measure_eyes=self._eyes_wanted(p)))
        else:
            self.engine.stop_source(SourceKind.WEBCAM)
        # the phone receiver is always on (so the phone's connect check and discovery work)
        self.engine.set_source(SourceKind.PHONE, PhoneSource(p.phone.track_port, self._discovery_reply))

    def apply_discovery(self) -> None:
        if self.discovery is not None:
            self.discovery.close()
            self.discovery = None
        self.discovery_error = None
        if not self.profile.phone.discovery_enabled:
            return
        try:
            self.discovery = DiscoveryResponder(self.profile.phone.discovery_port, self.profile.phone.track_port,
                                                self.pc_name, self.engine.capabilities)
            self.discovery.start()
        except OSError as e:
            self.discovery_error = f"Discovery port {self.profile.phone.discovery_port} unavailable: {e}"
            self.log(self.discovery_error)

    def apply_api(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None
        a = self.profile.api
        if not a.enabled:
            return
        self.server = StateServer(self.engine.api_state, a.port, a.bind)
        self.server.start()
        if self.server.error:
            self.log(self.server.error)

    def gaze_warp(self) -> None:
        if self._mouse is not None:
            self._mouse.warp()

    def _discovery_reply(self, nonce: int):
        from . import protocol
        return protocol.encode_discovery_reply(self.profile.phone.track_port, nonce, self.engine.capabilities(), self.pc_name())

    def update_profile(self, new: TrackingProfile, save: bool = True) -> None:
        old = self.profile
        self.profile = new
        self.engine.update_profile(new)
        if new.output != old.output or new.mouse.mode != old.mouse.mode:
            self.apply_outputs()
        if new.api != old.api:
            self.apply_api()
        if (new.source != old.source or new.camera != old.camera or new.phone.track_port != old.phone.track_port
                or self._eyes_wanted(new) != self._eyes_wanted(old)):
            self.apply_sources()
        if new.phone != old.phone:
            self.apply_discovery()
        if new.hotkeys != old.hotkeys:
            self.hotkeys.update(new.hotkeys)
            if new.hotkeys.enabled and self.hotkeys._thread is None:
                self.hotkeys.start()
        # Global settings always land in the base profile; tuning goes to the active game's file instead.
        if self.active_game_id > 0:
            self.base_profile = replace(self.base_profile, **{k: getattr(new, k) for k in ("source", "camera", "output", "phone", "hotkeys", "calibration", "mouse", "api", "screen_gaze")})
        else:
            self.base_profile = new
        if save:
            try:
                prof.save(self.base_profile, self.profile_path)
                if self.active_game_id > 0:
                    self.library.save_for_game(self.active_game_id, new)
            except OSError as e:
                self.log(f"Could not save profile: {e}")

    @staticmethod
    def _eyes_wanted(p: TrackingProfile) -> bool:
        """The eye model runs when any eye feature needs it: eye-assisted look, screen gaze, gaze mouse."""
        return p.eye_assist.enabled or bool(p.screen_gaze.weights) or p.mouse.mode in (MouseMode.GAZE_FOLLOW, MouseMode.GAZE_HOTKEY)

    def start_screen_calibration(self) -> None:
        """Needs the eye model: restart the camera with it if it is off, then begin."""
        if self.engine.source(SourceKind.WEBCAM) is not None and not getattr(self.engine.source(SourceKind.WEBCAM), "measure_eyes", False):
            self.engine.set_source(SourceKind.WEBCAM, WebcamSource(self.profile.camera, measure_eyes=True))
        self.engine.start_screen_calibration()

    # --- per-game tuning -----------------------------------------------------------------------
    def _on_engine_state(self, st: EngineState) -> None:
        if st.game_id != self.active_game_id:
            self.switch_game(st.game_id, st.game_name)

    def _on_engine_profile(self, p: TrackingProfile) -> None:
        # the engine changed settings itself (gaze compensation, screen calibration): keep and persist
        self.update_profile(replace(self.profile, eye_assist=p.eye_assist, screen_gaze=p.screen_gaze))

    def switch_game(self, game_id: int, game_name: str) -> None:
        """Loads the tuning for the game that just connected (or the base tuning when it closed)."""
        if game_id > 0:
            new = self.library.for_game(game_id, game_name, self.profile)
            self.active_game_id, self.active_game_name = game_id, game_name
            self.log(f"game connected: {game_name} ({game_id}) → tuning '{new.name}'")
        else:
            new = with_tuning(self.profile, self.base_profile, name=self.base_profile.name)
            self.active_game_id, self.active_game_name = 0, ""
            self.log("game closed → base tuning")
        self.update_profile(new, save=False)

    def apply_preset(self, name: str) -> None:
        preset = PRESETS[name]
        label = f"{self.active_game_name} ({preset.name})" if self.active_game_id > 0 else preset.name
        self.update_profile(with_tuning(self.profile, preset, name=label))

    def tuning_label(self) -> str:
        if self.active_game_id > 0:
            saved = "saved for this game" if self.library.has_saved(self.active_game_id) else f"{category_of(self.active_game_id)} preset"
            return f"{self.active_game_name}: {self.profile.name} ({saved}; edits are saved for this game)"
        return f"No game connected: {self.profile.name} (edits apply to every game without its own tuning)"

    # --- diagnostics --------------------------------------------------------------------------
    def self_check(self) -> selfcheck.SelfCheckReport:
        st = self.engine.state
        phone = self.engine.source(SourceKind.PHONE)
        stats = getattr(phone, "stats", None)
        packets = stats.snapshot().packets if stats is not None else 0
        return selfcheck.run(selfcheck.SelfCheckInput(
            windows=sys.platform == "win32",
            webcam_wanted=self.profile.source is SourceKind.WEBCAM,
            webcam_status=st.webcam_status.value, webcam_error=st.webcam_error,
            face_detected=st.raw is not None and st.webcam_status is SourceStatus.RUNNING and (time.monotonic_ns() - st.last_frame_nanos) < 1_000_000_000,
            fps=st.fps,
            seconds_since_webcam_start=(time.monotonic() - self._webcam_started_at) if self._webcam_started_at else 0.0,
            has_neutral=st.has_neutral,
            game_output_wanted=self.profile.output.freetrack_enabled,
            game_output_active=any(getattr(o, "is_game_output", False) for o in self.engine._outputs),
            game_output_error=next((n for n in self.output_notes if "NOT active" in n or "failed" in n.lower()), None),
            libs_present=find_libs_dir() is not None,
            game_id=st.game_id, game_name=st.game_name,
            udp_output=self.profile.output.udp_enabled,
            phone_status=st.phone_status.value, phone_error=st.phone_error, phone_packets=packets, phone_fresh=st.phone_fresh,
            discovery_on=self.discovery is not None, discovery_error=self.discovery_error,
            discoveries_answered=self.discovery.answered if self.discovery is not None else 0,
            firewall_rule_present=fixes.firewall_rule_present(),
            sweeping=self.engine.sweeping,
        ))

    def run_fix(self, action: str) -> str:
        extra = {"retry_camera": lambda: (self.apply_sources(), "Camera restarted")[1]}
        msg = fixes.run(action, extra)
        if action == "fetch_libs" and self.profile.output.freetrack_enabled:
            self.apply_outputs()
        self.log(f"fix {action}: {msg}")
        return msg

    # --- convenience ---------------------------------------------------------------------------
    def calibrate(self) -> None:
        self.engine.calibrate()

    def recenter(self) -> None:
        self.engine.recenter()
