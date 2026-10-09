"""Face-loss policy: hold, ease to neutral, stay neutral (port of core `pipeline.FaceLossPolicy`)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .pose import NEUTRAL, HeadPose


@dataclass(frozen=True)
class FaceLossSettings:
    hold_millis: int = 300
    return_millis: int = 600

    def __post_init__(self):
        if self.hold_millis < 0 or self.return_millis < 0:
            raise ValueError("face-loss timings must be >= 0")


class TrackingState(Enum):
    TRACKING = "TRACKING"
    HOLDING = "HOLDING"
    RETURNING = "RETURNING"
    NEUTRAL = "NEUTRAL"


class FaceLossHandler:
    """Hold the last good output briefly, then ease to neutral; the output never jumps."""

    def __init__(self, settings: FaceLossSettings):
        self.settings = settings
        self.state = TrackingState.NEUTRAL
        self.last_good = NEUTRAL
        self.lost_at = 0

    def update_settings(self, s: FaceLossSettings) -> None:
        self.settings = s

    def update(self, output: Optional[HeadPose], now_nanos: int) -> HeadPose:
        if output is not None and output.is_finite:
            self.last_good = output
            self.state = TrackingState.TRACKING
            return output
        if self.state is TrackingState.TRACKING:
            self.lost_at = now_nanos
        elapsed_ms = (now_nanos - self.lost_at) // 1_000_000
        s = self.settings
        if self.state is TrackingState.NEUTRAL:
            return NEUTRAL.with_time(now_nanos)
        if elapsed_ms < s.hold_millis:
            self.state = TrackingState.HOLDING
            return self.last_good.with_time(now_nanos)
        if elapsed_ms < s.hold_millis + s.return_millis:
            self.state = TrackingState.RETURNING
            t = (elapsed_ms - s.hold_millis) / s.return_millis
            return self.last_good.scaled(1.0 - max(0.0, min(1.0, t))).with_time(now_nanos)
        self.state = TrackingState.NEUTRAL
        return NEUTRAL.with_time(now_nanos)

    def reset(self) -> None:
        self.state = TrackingState.NEUTRAL
        self.last_good = NEUTRAL
        self.lost_at = 0


