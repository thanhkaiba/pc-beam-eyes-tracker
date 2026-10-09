"""Eye gaze estimate and eye-assisted look (ports of core `gaze`).

Conventions: horizontal > 0 = looking toward the person's RIGHT, vertical > 0 = UP; unitless,
roughly -1..1. Eye-assisted look is experimental and off by default: a sideways glance adds yaw
on top of the head pose so a short look at the side turns the in-game view further.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from .pose import HeadPose


@dataclass(frozen=True)
class Point2:
    x: float
    y: float


@dataclass(frozen=True)
class IrisPosition:
    across: float
    down: float


@dataclass(frozen=True)
class EyeSignals:
    look_in_left: float = 0.0
    look_out_left: float = 0.0
    look_up_left: float = 0.0
    look_down_left: float = 0.0
    look_in_right: float = 0.0
    look_out_right: float = 0.0
    look_up_right: float = 0.0
    look_down_right: float = 0.0
    blink_left: float = 0.0
    blink_right: float = 0.0
    iris_image_left: Optional[IrisPosition] = None
    iris_image_right: Optional[IrisPosition] = None


@dataclass(frozen=True)
class GazeReading:
    blend_horizontal: float
    blend_vertical: float
    iris_horizontal: float
    iris_vertical: float
    eyes_open: bool
    timestamp_nanos: int


BLINK_CLOSED = 0.5
LOOK_DOWN_NOT_BLINK = 0.4
IRIS_VERTICAL_GAIN = 5.0
# MediaPipe face mesh indices (478-point model with irises): corner, corner, upper lid, lower lid, iris centre
IMAGE_LEFT_EYE = (33, 133, 159, 145, 468)
IMAGE_RIGHT_EYE = (362, 263, 386, 374, 473)


def _clamp1(v: float) -> float:
    return v if math.isnan(v) else max(-1.0, min(1.0, v))


def estimate(e: EyeSignals, mirrored: bool, timestamp_nanos: int) -> GazeReading:
    bh = ((e.look_in_left - e.look_out_left) + (e.look_out_right - e.look_in_right)) / 2.0
    bv = ((e.look_up_left - e.look_down_left) + (e.look_up_right - e.look_down_right)) / 2.0
    irises = [i for i in (e.iris_image_left, e.iris_image_right) if i is not None]
    if irises:
        ih = (0.5 - sum(i.across for i in irises) / len(irises)) * 2.0
        iv = -(sum(i.down for i in irises) / len(irises)) * IRIS_VERTICAL_GAIN
    else:
        ih = iv = math.nan
    if mirrored:
        bh, ih = -bh, -ih
    looking_down = (e.look_down_left + e.look_down_right) / 2.0 >= LOOK_DOWN_NOT_BLINK
    open_ = looking_down or (e.blink_left < BLINK_CLOSED and e.blink_right < BLINK_CLOSED)
    return GazeReading(_clamp1(bh), _clamp1(bv), _clamp1(ih), _clamp1(iv), open_, timestamp_nanos)


def iris_position(corner_a: Point2, corner_b: Point2, upper: Point2, lower: Point2, iris: Point2) -> Optional[IrisPosition]:
    left, right = min(corner_a.x, corner_b.x), max(corner_a.x, corner_b.x)
    width = right - left
    height = lower.y - upper.y
    if width <= 1e-6 or height <= 1e-6:
        return None
    across = max(0.0, min(1.0, (iris.x - left) / width))
    corner_mid_y = (corner_a.y + corner_b.y) / 2.0
    down = max(-0.5, min(0.5, (iris.y - corner_mid_y) / width))
    return IrisPosition(across, down)


def eye_signals_from_result(blendshapes, landmarks) -> Optional[EyeSignals]:
    """Builds EyeSignals from a MediaPipe FaceLandmarkerResult's first face (None without blendshapes)."""
    if not blendshapes:
        return None
    score = {c.category_name: float(c.score) for c in blendshapes}

    def s(name: str) -> float:
        return score.get(name, 0.0)

    def iris(idx):
        if landmarks is None or len(landmarks) <= max(idx):
            return None
        pts = [Point2(float(landmarks[i].x), float(landmarks[i].y)) for i in idx]
        return iris_position(*pts)

    return EyeSignals(
        s("eyeLookInLeft"), s("eyeLookOutLeft"), s("eyeLookUpLeft"), s("eyeLookDownLeft"),
        s("eyeLookInRight"), s("eyeLookOutRight"), s("eyeLookUpRight"), s("eyeLookDownRight"),
        s("eyeBlinkLeft"), s("eyeBlinkRight"), iris(IMAGE_LEFT_EYE), iris(IMAGE_RIGHT_EYE),
    )


class GazeSource(Enum):
    MODEL = "Model scores"
    IRIS = "Iris position"
    SCREEN = "Screen gaze (calibrated)"


@dataclass(frozen=True)
class EyeAssistSettings:
    enabled: bool = False
    source: GazeSource = GazeSource.MODEL
    dead_zone: float = 0.25
    gain_degrees: float = 30.0
    max_degrees: float = 30.0
    smoothing_seconds: float = 0.15
    head_compensation: bool = False
    compensation_scale: float = 0.0
    compensation_bias: float = 0.0
    compensation_source: Optional[GazeSource] = None
    # Extended view (SCREEN source): vertical gain/limit; horizontal uses gain_degrees/max_degrees.
    vertical: bool = True
    gain_degrees_y: float = 15.0
    max_degrees_y: float = 15.0

    def __post_init__(self):
        if not (math.isfinite(self.dead_zone) and 0.0 <= self.dead_zone <= 0.95):
            raise ValueError("deadZone must be within 0..0.95")
        if not (math.isfinite(self.gain_degrees) and self.gain_degrees >= 0):
            raise ValueError("gainDegrees must be >= 0")
        if not (math.isfinite(self.max_degrees) and self.max_degrees >= 0):
            raise ValueError("maxDegrees must be >= 0")
        if not (math.isfinite(self.smoothing_seconds) and 0.0 <= self.smoothing_seconds <= 2.0):
            raise ValueError("smoothingSeconds must be within 0..2")

    @property
    def compensation_ready(self) -> bool:
        return self.compensation_scale > 0.0 and self.compensation_source == self.source


SNAP_DEGREES = 0.01


def _shape(h: float, dead_zone: float, gain: float, limit: float) -> float:
    a = abs(h)
    if a <= dead_zone:
        return 0.0
    scaled = (a - dead_zone) / (1.0 - dead_zone) * gain
    return math.copysign(min(scaled, limit), h)


class EyeAssist:
    """Stateful stage applied after the head pipeline; adds yaw (and pitch with the SCREEN source)
    only while tracked, calibrated and eyes open. With the SCREEN source the glance is the
    calibrated gaze point relative to the screen centre (-1..1 each axis): the "extended view"."""

    def __init__(self, settings: EyeAssistSettings = EyeAssistSettings()):
        self.settings = settings
        self.yaw_degrees = 0.0
        self.pitch_degrees = 0.0
        self._last_nanos: Optional[int] = None

    def update(self, s: EyeAssistSettings) -> None:
        self.settings = s

    def reset(self) -> None:
        self.yaw_degrees = 0.0
        self.pitch_degrees = 0.0
        self._last_nanos = None

    def target(self, reading: Optional[GazeReading], head_yaw_degrees: float = 0.0,
               screen: Optional[Tuple[float, float]] = None) -> Tuple[float, float]:
        """(yaw, pitch) to add. `screen` = gaze point (x, y) in 0..1 for the SCREEN source."""
        s = self.settings
        if not s.enabled:
            return (0.0, 0.0)
        if s.source is GazeSource.SCREEN:
            if screen is None or not all(math.isfinite(v) for v in screen):
                return (0.0, 0.0)
            if reading is not None and not reading.eyes_open:
                return (0.0, 0.0)
            hx = max(-1.0, min(1.0, (screen[0] - 0.5) * 2.0))
            hy = max(-1.0, min(1.0, (0.5 - screen[1]) * 2.0))  # up is positive
            yaw = _shape(hx, s.dead_zone, s.gain_degrees, s.max_degrees)
            pitch = _shape(hy, s.dead_zone, s.gain_degrees_y, s.max_degrees_y) if s.vertical else 0.0
            return (yaw, pitch)
        if reading is None or not reading.eyes_open:
            return (0.0, 0.0)
        h = reading.blend_horizontal if s.source is GazeSource.MODEL else reading.iris_horizontal
        if not math.isfinite(h):
            return (0.0, 0.0)
        if s.head_compensation:
            if not s.compensation_ready or not math.isfinite(head_yaw_degrees):
                return (0.0, 0.0)
            h = h - s.compensation_bias + s.compensation_scale * head_yaw_degrees
        return (_shape(h, s.dead_zone, s.gain_degrees, s.max_degrees), 0.0)

    def apply(self, pose: HeadPose, reading: Optional[GazeReading], active: bool, now_nanos: int,
              head_yaw_degrees: float = 0.0, screen: Optional[Tuple[float, float]] = None) -> HeadPose:
        goal_yaw, goal_pitch = self.target(reading, head_yaw_degrees, screen) if active else (0.0, 0.0)
        tau = self.settings.smoothing_seconds
        if tau <= 0.0:
            self.yaw_degrees, self.pitch_degrees = goal_yaw, goal_pitch
        elif self._last_nanos is None:
            self.yaw_degrees = self.pitch_degrees = 0.0
        else:
            dt = max(0.0, min(1.0, (now_nanos - self._last_nanos) / 1e9))
            k = 1.0 - math.exp(-dt / tau)
            self.yaw_degrees += (goal_yaw - self.yaw_degrees) * k
            self.pitch_degrees += (goal_pitch - self.pitch_degrees) * k
        self._last_nanos = now_nanos
        if abs(self.yaw_degrees) < SNAP_DEGREES:
            self.yaw_degrees = 0.0
        if abs(self.pitch_degrees) < SNAP_DEGREES:
            self.pitch_degrees = 0.0
        if self.yaw_degrees == 0.0 and self.pitch_degrees == 0.0:
            return pose
        from dataclasses import replace
        return replace(pose, yaw=max(-180.0, min(180.0, pose.yaw + self.yaw_degrees)),
                       pitch=max(-180.0, min(180.0, pose.pitch + self.pitch_degrees)))


class CompensationFailure(Enum):
    TOO_FEW_SAMPLES = "Not enough frames with your eyes open and face tracked. Keep your face in view and try again."
    TOO_LITTLE_HEAD_TURN = "Turn your head further to both sides (about 20° each way) while looking at the screen centre."
    WRONG_DIRECTION = "Your eyes did not move against your head turn. Keep looking at the centre of the screen while turning."
    TOO_NOISY = "The eye signal was too noisy to fit. Add light on your face and turn more slowly."
    IMPLAUSIBLE = "The measured scale is outside the expected range. Try again, or try the other gaze source."


@dataclass(frozen=True)
class CompensationResult:
    scale: float = 0.0
    bias: float = 0.0
    r2: float = 0.0
    yaw_range_degrees: float = 0.0
    samples: int = 0
    failure: Optional[CompensationFailure] = None

    @property
    def ok(self) -> bool:
        return self.failure is None


class GazeCompensationCalibrator:
    """While the user looks at the screen centre and turns the head, H ≈ bias − scale × yaw; a least-squares fit."""

    DEFAULT_DURATION_NANOS = 6_000_000_000

    def __init__(self, duration_nanos: int = DEFAULT_DURATION_NANOS, min_yaw_range: float = 15.0,
                 min_samples: int = 40, min_r2: float = 0.5, scale_range=(0.002, 0.2)):
        self.duration_nanos = duration_nanos
        self.min_yaw_range, self.min_samples, self.min_r2, self.scale_range = min_yaw_range, min_samples, min_r2, scale_range
        self.yaws: List[float] = []
        self.hs: List[float] = []
        self.start_nanos = 0
        self.running = False

    @property
    def samples(self) -> int:
        return len(self.yaws)

    @property
    def yaw_range_degrees(self) -> float:
        return (max(self.yaws) - min(self.yaws)) if self.yaws else 0.0

    def begin(self, now_nanos: int) -> None:
        self.yaws.clear()
        self.hs.clear()
        self.start_nanos = now_nanos
        self.running = True

    def progress(self, now_nanos: int) -> float:
        return max(0.0, min(1.0, (now_nanos - self.start_nanos) / self.duration_nanos)) if self.running else 0.0

    def elapsed(self, now_nanos: int) -> bool:
        return self.running and now_nanos - self.start_nanos >= self.duration_nanos

    def add(self, head_yaw_degrees: float, h: float, eyes_open: bool) -> None:
        if not self.running or not eyes_open or not math.isfinite(head_yaw_degrees) or not math.isfinite(h):
            return
        self.yaws.append(head_yaw_degrees)
        self.hs.append(h)

    def cancel(self) -> None:
        self.running = False
        self.yaws.clear()
        self.hs.clear()

    def finish(self) -> CompensationResult:
        self.running = False
        n = len(self.yaws)
        rng = self.yaw_range_degrees
        if n < self.min_samples:
            return CompensationResult(samples=n, yaw_range_degrees=rng, failure=CompensationFailure.TOO_FEW_SAMPLES)
        if rng < self.min_yaw_range:
            return CompensationResult(samples=n, yaw_range_degrees=rng, failure=CompensationFailure.TOO_LITTLE_HEAD_TURN)
        my = sum(self.yaws) / n
        mh = sum(self.hs) / n
        sxy = sxx = syy = 0.0
        for y, h in zip(self.yaws, self.hs):
            dx, dy = y - my, h - mh
            sxy += dx * dy
            sxx += dx * dx
            syy += dy * dy
        slope = sxy / sxx
        intercept = mh - slope * my
        r2 = 0.0 if syy <= 0.0 else (sxy * sxy) / (sxx * syy)
        if slope >= 0.0:
            return CompensationResult(samples=n, yaw_range_degrees=rng, failure=CompensationFailure.WRONG_DIRECTION)
        if r2 < self.min_r2:
            return CompensationResult(samples=n, yaw_range_degrees=rng, failure=CompensationFailure.TOO_NOISY)
        scale = -slope
        if not (self.scale_range[0] <= scale <= self.scale_range[1]):
            return CompensationResult(samples=n, yaw_range_degrees=rng, failure=CompensationFailure.IMPLAUSIBLE)
        return CompensationResult(scale, intercept, r2, rng, n)
