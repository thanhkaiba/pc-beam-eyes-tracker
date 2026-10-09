"""Mouse output: for games without TrackIR support, and cursor placement by gaze.

- Head mouse (relative): yaw/pitch changes become mouse movement (`SendInput` MOUSEEVENTF_MOVE),
  like opentrack's mouse emulation. Works in any game that looks around with the mouse.
- Gaze cursor (absolute): the cursor warps to the gaze point when a hotkey is pressed, or follows
  it continuously; a desktop/accessibility feature.

The maths is pure and tested; only `WindowsMouse` touches the OS.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from ..pose import HeadPose


class MouseMode(Enum):
    OFF = "off"
    HEAD = "head"          # relative movement from yaw/pitch
    GAZE_FOLLOW = "gaze"   # cursor follows the gaze point
    GAZE_HOTKEY = "gaze_hotkey"  # cursor warps to the gaze point on the hotkey


@dataclass(frozen=True)
class MouseSettings:
    mode: MouseMode = MouseMode.OFF
    pixels_per_degree_x: float = 12.0
    pixels_per_degree_y: float = 12.0
    invert_y: bool = False
    dead_zone_degrees: float = 0.3
    gaze_smoothing: float = 0.5   # 0 = snap, 1 = very smooth (gaze modes)

    def __post_init__(self):
        for v in (self.pixels_per_degree_x, self.pixels_per_degree_y, self.dead_zone_degrees, self.gaze_smoothing):
            if not math.isfinite(v) or v < 0:
                raise ValueError("mouse settings must be finite and >= 0")


class HeadMouseMapper:
    """Accumulates fractional pixels so slow movements are not lost to integer rounding."""

    def __init__(self, settings: MouseSettings):
        self.settings = settings
        self._last: Optional[HeadPose] = None
        self._acc_x = 0.0
        self._acc_y = 0.0

    def reset(self) -> None:
        self._last = None
        self._acc_x = self._acc_y = 0.0

    def delta(self, pose: Optional[HeadPose]) -> Tuple[int, int]:
        """Integer mouse movement for this frame (dx right, dy down)."""
        s = self.settings
        if pose is None or not pose.is_finite:
            self._last = None
            return (0, 0)
        if self._last is None:
            self._last = pose
            return (0, 0)
        dyaw = pose.yaw - self._last.yaw
        dpitch = pose.pitch - self._last.pitch
        self._last = pose
        if abs(dyaw) < s.dead_zone_degrees:
            dyaw = 0.0
        if abs(dpitch) < s.dead_zone_degrees:
            dpitch = 0.0
        self._acc_x += dyaw * s.pixels_per_degree_x
        self._acc_y += (-dpitch if not s.invert_y else dpitch) * s.pixels_per_degree_y  # looking up moves the view up (mouse up = negative dy)
        dx, dy = int(self._acc_x), int(self._acc_y)
        self._acc_x -= dx
        self._acc_y -= dy
        return (dx, dy)


class MouseBackend:
    def move_relative(self, dx: int, dy: int) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def move_absolute(self, x: int, y: int) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def screen_size(self) -> Tuple[int, int]:  # pragma: no cover - interface
        raise NotImplementedError


class WindowsMouse(MouseBackend):
    def __init__(self):
        import ctypes
        self._c = ctypes
        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT), ("pad", ctypes.c_ubyte * 8)]
        self._INPUT = INPUT

    def move_relative(self, dx: int, dy: int) -> None:
        if dx == 0 and dy == 0:
            return
        inp = self._INPUT()
        inp.type = 0  # INPUT_MOUSE
        inp.mi.dx, inp.mi.dy, inp.mi.dwFlags = int(dx), int(dy), 0x0001  # MOUSEEVENTF_MOVE
        self._user32.SendInput(1, self._c.byref(inp), self._c.sizeof(inp))

    def move_absolute(self, x: int, y: int) -> None:
        self._user32.SetCursorPos(int(x), int(y))

    def screen_size(self) -> Tuple[int, int]:
        return int(self._user32.GetSystemMetrics(0)), int(self._user32.GetSystemMetrics(1))


class MouseOutput:
    """Engine output. `write` receives the game pose; `gaze(x, y)` the screen gaze point (0..1)."""
    name = "mouse"
    is_game_output = False
    game_name = ""
    game_id = 0

    def __init__(self, settings: MouseSettings, backend: Optional[MouseBackend] = None):
        self.settings = settings
        self.backend = backend
        if self.backend is None:
            if sys.platform != "win32":
                raise RuntimeError("mouse output needs Windows")
            self.backend = WindowsMouse()
        self._head = HeadMouseMapper(settings)
        self._gx: Optional[float] = None
        self._gy: Optional[float] = None
        self.warp_requested = False
        self.active = False  # paused by the toggle hotkey → no movement

    def update_settings(self, s: MouseSettings) -> None:
        self.settings = s
        self._head.settings = s

    def write(self, pose, raw=None, flags=0, timestamp_nanos=0) -> None:
        if self.settings.mode is MouseMode.HEAD and self.active:
            dx, dy = self._head.delta(pose)
            if dx or dy:
                self.backend.move_relative(dx, dy)
        elif self.settings.mode is not MouseMode.HEAD:
            self._head.reset()

    def gaze(self, x: Optional[float], y: Optional[float]) -> None:
        if x is None or y is None:
            return
        s = self.settings
        if self._gx is None or s.gaze_smoothing <= 0:
            self._gx, self._gy = x, y
        else:
            a = 1.0 - s.gaze_smoothing
            self._gx += a * (x - self._gx)
            self._gy += a * (y - self._gy)
        if s.mode is MouseMode.GAZE_FOLLOW and self.active or (s.mode is MouseMode.GAZE_HOTKEY and self.warp_requested):
            self.warp_requested = False
            w, h = self.backend.screen_size()
            self.backend.move_absolute(int(max(0.0, min(1.0, self._gx)) * (w - 1)), int(max(0.0, min(1.0, self._gy)) * (h - 1)))

    def warp(self) -> None:
        self.warp_requested = True

    def close(self) -> None:
        pass
