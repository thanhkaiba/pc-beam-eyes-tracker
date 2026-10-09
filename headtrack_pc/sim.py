"""Direction check: scripted one-axis sweep and the cockpit preview maths (ports of core `sim`)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from .pose import NAN, Axis, HeadPose


@dataclass(frozen=True)
class SweepPhase:
    axis: Optional[Axis]
    label: str
    expect: str
    delta: float
    seconds: float

    @property
    def is_centre(self) -> bool:
        return self.axis is None


MOVE_SECONDS = 2.5
CENTRE_SECONDS = 1.2
RAMP_SECONDS = 0.8


def _centre(label: str = "Centre") -> SweepPhase:
    return SweepPhase(None, label, "The game view returns to straight ahead", 0.0, CENTRE_SECONDS)


PHASES: List[SweepPhase] = [
    _centre("Start at centre"),
    SweepPhase(Axis.YAW, "Yaw right", "The game view turns right", 25.0, MOVE_SECONDS),
    _centre(),
    SweepPhase(Axis.YAW, "Yaw left", "The game view turns left", -25.0, MOVE_SECONDS),
    _centre(),
    SweepPhase(Axis.PITCH, "Pitch up", "The game view tilts up", 15.0, MOVE_SECONDS),
    _centre(),
    SweepPhase(Axis.PITCH, "Pitch down", "The game view tilts down", -15.0, MOVE_SECONDS),
    _centre(),
    SweepPhase(Axis.ROLL, "Roll right", "Right ear down: the horizon tilts", 15.0, MOVE_SECONDS),
    _centre(),
    SweepPhase(Axis.ROLL, "Roll left", "Left ear down: the horizon tilts the other way", -15.0, MOVE_SECONDS),
    _centre("Back at centre"),
]
TOTAL_SECONDS = sum(p.seconds for p in PHASES)


@dataclass(frozen=True)
class SweepPosition:
    index: int
    phase: SweepPhase
    seconds_into_phase: float

    @property
    def phase_progress(self) -> float:
        return max(0.0, min(1.0, self.seconds_into_phase / self.phase.seconds))


def position_at(seconds: float) -> Optional[SweepPosition]:
    if seconds < 0:
        return None
    t = seconds
    for i, p in enumerate(PHASES):
        if t < p.seconds:
            return SweepPosition(i, p, t)
        t -= p.seconds
    return None


def progress(seconds: float) -> float:
    return max(0.0, min(1.0, seconds / TOTAL_SECONDS))


def _smoothstep(x: float) -> float:
    t = max(0.0, min(1.0, x))
    return t * t * (3 - 2 * t)


def envelope(seconds_into_phase: float, phase_seconds: float) -> float:
    up = _smoothstep(seconds_into_phase / RAMP_SECONDS)
    down = 1 - _smoothstep((seconds_into_phase - (phase_seconds - RAMP_SECONDS)) / RAMP_SECONDS)
    return max(0.0, min(1.0, up * down))


def pose_at(seconds: float, centre: HeadPose, timestamp_nanos: Optional[int] = None) -> Optional[HeadPose]:
    """The raw pose to feed at `seconds`: `centre` plus the current phase's eased delta; None when finished."""
    pos = position_at(seconds)
    if pos is None:
        return None
    p = pos.phase
    a = centre.to_array()
    if p.axis is not None:
        a[p.axis.index] += p.delta * envelope(pos.seconds_into_phase, p.seconds)
    ts = int(seconds * 1e9) if timestamp_nanos is None else timestamp_nanos
    return HeadPose.from_array(a, timestamp_nanos=ts, confidence=NAN)


# --- cockpit preview --------------------------------------------------------------------------

FOV_H = 90.0
FOV_V = 60.0
MAX_YAW = 100.0
MAX_PITCH = 40.0
PARALLAX_CM = 20.0
ZOOM_CM = 20.0


@dataclass(frozen=True)
class Camera:
    pan_x: float
    pan_y: float
    roll_degrees: float
    parallax_x: float
    parallax_y: float
    zoom: float
    words: str


def camera(sent: Optional[HeadPose]) -> Camera:
    """How a driving game would move its in-car camera for the pose being sent."""
    if sent is None or not sent.is_finite:
        return Camera(0.0, 0.0, 0.0, 0.0, 0.0, 1.0, "straight ahead (nothing sent yet)")
    yaw = max(-MAX_YAW, min(MAX_YAW, sent.yaw))
    pitch = max(-MAX_PITCH, min(MAX_PITCH, sent.pitch))
    pan_x = -yaw / FOV_H
    pan_y = pitch / FOV_V
    roll = -max(-90.0, min(90.0, sent.roll))
    parallax_x = -(sent.x / PARALLAX_CM) * 0.1
    parallax_y = (sent.y / PARALLAX_CM) * 0.1
    zoom = max(0.7, min(1.5, 1 + (sent.z / ZOOM_CM) * 0.1))
    if yaw > 55:
        where = "right window"
    elif yaw > 15:
        where = "right mirror"
    elif yaw < -55:
        where = "left window"
    elif yaw < -15:
        where = "left mirror"
    elif pitch > 20:
        where = "rear-view mirror"
    elif pitch < -20:
        where = "dashboard"
    elif abs(yaw) < 5 and abs(pitch) < 5:
        where = "road ahead"
    else:
        where = "windscreen"
    words = f"{where} (yaw {yaw:+.0f}°, pitch {pitch:+.0f}°, roll {sent.roll:+.0f}°)"
    return Camera(pan_x, pan_y, roll, parallax_x, parallax_y, zoom, words)
