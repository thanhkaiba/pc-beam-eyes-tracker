"""Per-axis mapping: dead zone → sensitivity → curve → clamp → invert (port of core `mapping`)."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum

from .pose import Axis, HeadPose


class ResponseCurve(Enum):
    LINEAR = "Linear"
    SOFT = "Soft"
    SQUARED = "Squared"
    CUBIC = "Cubic"

    @property
    def label(self) -> str:
        return self.value

    def apply(self, v: float) -> float:
        if not math.isfinite(v):
            return 0.0
        c = max(-1.0, min(1.0, v))
        a = abs(c)
        s = math.copysign(1.0, c) if c != 0 else 0.0
        if self is ResponseCurve.LINEAR:
            return c
        if self is ResponseCurve.SOFT:
            return s * a ** 1.5
        if self is ResponseCurve.SQUARED:
            return s * a * a
        return s * a * a * a


@dataclass(frozen=True)
class AxisSettings:
    enabled: bool = True
    sensitivity: float = 1.0
    inverted: bool = False
    dead_zone: float = 0.0
    max_output: float = 90.0
    curve: ResponseCurve = ResponseCurve.LINEAR

    def __post_init__(self):
        if not (math.isfinite(self.sensitivity) and self.sensitivity >= 0.0):
            raise ValueError("sensitivity must be finite and >= 0")
        if not (math.isfinite(self.dead_zone) and self.dead_zone >= 0.0):
            raise ValueError("deadZone must be finite and >= 0")
        if not (math.isfinite(self.max_output) and self.max_output > 0.0):
            raise ValueError("maxOutput must be finite and > 0")


ROTATION_DEFAULT = AxisSettings(sensitivity=1.0, dead_zone=1.0, max_output=90.0)
TRANSLATION_DEFAULT = AxisSettings(enabled=False, sensitivity=1.0, dead_zone=0.5, max_output=30.0)


def map_axis(value: float, s: AxisSettings) -> float:
    """Pure per-axis mapping, identical in order to the Android core (see docs/calibration-guide.md there)."""
    if not s.enabled or not math.isfinite(value):
        return 0.0
    a = abs(value)
    if a <= s.dead_zone:
        return 0.0
    span = s.max_output - s.dead_zone
    after_dead = (a - s.dead_zone) * (s.max_output / span) if span > 0 else (a - s.dead_zone)
    sensitive = after_dead * s.sensitivity
    normalised = max(0.0, min(1.0, sensitive / s.max_output))
    curved = s.curve.apply(normalised) * s.max_output
    clamped = max(0.0, min(s.max_output, curved))
    signed = math.copysign(clamped, value)
    return -signed if s.inverted else signed


@dataclass(frozen=True)
class MappingSettings:
    yaw: AxisSettings = ROTATION_DEFAULT
    pitch: AxisSettings = ROTATION_DEFAULT
    roll: AxisSettings = ROTATION_DEFAULT
    x: AxisSettings = TRANSLATION_DEFAULT
    y: AxisSettings = TRANSLATION_DEFAULT
    z: AxisSettings = TRANSLATION_DEFAULT

    def get(self, axis: Axis) -> AxisSettings:
        return getattr(self, axis.name.lower())

    def with_axis(self, axis: Axis, s: AxisSettings) -> "MappingSettings":
        return replace(self, **{axis.name.lower(): s})

    def map(self, calibrated: HeadPose) -> HeadPose:
        return HeadPose(
            yaw=map_axis(calibrated.yaw, self.yaw),
            pitch=map_axis(calibrated.pitch, self.pitch),
            roll=map_axis(calibrated.roll, self.roll),
            x=map_axis(calibrated.x, self.x),
            y=map_axis(calibrated.y, self.y),
            z=map_axis(calibrated.z, self.z),
            timestamp_nanos=calibrated.timestamp_nanos,
            confidence=calibrated.confidence,
        )
