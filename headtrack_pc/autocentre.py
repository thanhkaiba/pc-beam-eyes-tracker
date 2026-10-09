"""Automatic centre correction: when the head has been still for a while close to the current
centre, the centre drifts toward that resting pose. Posture creep over a long session is
corrected without the user pressing Recenter, while a deliberate long look to the side (beyond
`max_offset_degrees`) or any movement leaves the centre alone. The drift is gradual
(`rate_per_second` of the remaining offset per second) so the game view never jumps.

Translation drifts along with rotation. Pure and deterministic; the engine owns the instance.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

from . import pose as posemath
from .calibration import mean_pose
from .pose import HeadPose


@dataclass(frozen=True)
class AutoCentreSettings:
    enabled: bool = True
    still_seconds: float = 2.0
    still_threshold_degrees: float = 1.5
    max_offset_degrees: float = 12.0
    rate_per_second: float = 0.5

    def __post_init__(self):
        if not (math.isfinite(self.still_seconds) and self.still_seconds > 0):
            raise ValueError("still_seconds must be > 0")
        if not (math.isfinite(self.rate_per_second) and self.rate_per_second >= 0):
            raise ValueError("rate_per_second must be >= 0")


class AutoCentre:
    def __init__(self, settings: AutoCentreSettings = AutoCentreSettings()):
        self.settings = settings
        self._window: Deque[Tuple[int, HeadPose]] = deque()
        self._last_nanos: Optional[int] = None
        self.active = False  # True while a drift is being applied (for the UI)

    def update_settings(self, s: AutoCentreSettings) -> None:
        self.settings = s

    def reset(self) -> None:
        self._window.clear()
        self._last_nanos = None
        self.active = False

    def still(self) -> bool:
        """True when the window spans `still_seconds` and every rotation axis stayed within the threshold."""
        s = self.settings
        if len(self._window) < 2 or (self._window[-1][0] - self._window[0][0]) < s.still_seconds * 1e9:
            return False
        for idx in range(3):
            vals = [p.to_array()[idx] for _, p in self._window]
            if max(vals) - min(vals) > s.still_threshold_degrees:
                return False
        return True

    def update(self, raw: Optional[HeadPose], neutral: Optional[HeadPose], now_nanos: int) -> Optional[HeadPose]:
        """Feeds one frame; returns a new neutral to apply, or None."""
        s = self.settings
        dt = 0.0 if self._last_nanos is None else max(0.0, min(1.0, (now_nanos - self._last_nanos) / 1e9))
        self._last_nanos = now_nanos
        if not s.enabled or raw is None or not raw.is_finite or neutral is None:
            self._window.clear()
            self.active = False
            return None
        self._window.append((now_nanos, raw))
        cutoff = now_nanos - int(s.still_seconds * 1e9)
        while self._window and self._window[0][0] < cutoff - 100_000_000:
            self._window.popleft()
        if not self.still():
            self.active = False
            return None
        resting = mean_pose([p for _, p in self._window])
        delta = posemath.relative_to(resting, neutral)
        if max(abs(delta.yaw), abs(delta.pitch), abs(delta.roll)) > s.max_offset_degrees:
            self.active = False
            return None
        if max(abs(delta.yaw), abs(delta.pitch), abs(delta.roll), abs(delta.x), abs(delta.y), abs(delta.z)) < 0.05:
            self.active = False
            return None
        k = 1.0 - math.exp(-dt * s.rate_per_second) if dt > 0 else 0.0
        if k <= 0.0:
            return None
        self.active = True
        return posemath.apply_relative(neutral, delta.scaled(k))
