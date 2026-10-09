"""Builds the engine, sources and outputs from a profile. Used by the GUI and the CLI."""
from __future__ import annotations

import sys
from dataclasses import replace
from typing import Callable, List, Optional

from . import profile as prof
from .engine import TrackingEngine
from .inputs.phone import PhoneSource
from .inputs.webcam import WebcamSource
from .net.discovery import DiscoveryResponder, default_pc_name
from .outputs.freetrack import FreetrackOutput, OutputUnavailable, find_libs_dir
from .outputs.udp import UdpOutput
from .profile import SourceKind, TrackingProfile


class GameOutputAdapter:
    """Engine `Output` wrapper around FreetrackOutput (adds the name and the game-output flag)."""
    name = "freetrack"
    is_game_output = True

    def __init__(self, inner: FreetrackOutput):
        self.inner = inner

    @property
    def game_name(self) -> str:
        return self.inner.game_name

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
                 log: Callable[[str], None] = print):
        self.profile_path = profile_path
        self.profile = profile if profile is not None else prof.load(profile_path)
        self.profile = replace(self.profile, neutral_pose=None)  # the centre is set every launch
        self.log = log
        self.engine = TrackingEngine(self.profile)
        self.discovery: Optional[DiscoveryResponder] = None
        self.output_notes: List[str] = []
        self.discovery_error: Optional[str] = None

    # --- lifecycle ---------------------------------------------------------------------------
    def start(self) -> None:
        self.engine.start()
        self.apply_outputs()
        self.apply_sources()
        self.apply_discovery()

    def stop(self) -> None:
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
        for n in self.output_notes:
            self.log(n)

    def apply_sources(self) -> None:
        p = self.profile
        if p.source is SourceKind.WEBCAM:
            self.engine.set_source(SourceKind.WEBCAM, WebcamSource(p.camera))
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

    def _discovery_reply(self, nonce: int):
        from . import protocol
        return protocol.encode_discovery_reply(self.profile.phone.track_port, nonce, self.engine.capabilities(), self.pc_name())

    def update_profile(self, new: TrackingProfile, save: bool = True) -> None:
        old = self.profile
        self.profile = new
        self.engine.update_profile(new)
        if new.output != old.output:
            self.apply_outputs()
        if new.source != old.source or new.camera != old.camera or new.phone.track_port != old.phone.track_port:
            self.apply_sources()
        if new.phone != old.phone:
            self.apply_discovery()
        if save:
            try:
                prof.save(new, self.profile_path)
            except OSError as e:
                self.log(f"Could not save profile: {e}")

    # --- convenience ---------------------------------------------------------------------------
    def calibrate(self) -> None:
        self.engine.calibrate()

    def recenter(self) -> None:
        self.engine.recenter()
