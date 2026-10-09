"""Where on the screen the user is looking (gaze point), from a webcam.

Features per frame: iris position inside each eye (from MediaPipe's iris landmarks) and the
head pose. A short calibration (look at 9 points) fits a ridge regression from those features
to screen coordinates, with a few quadratic/cross terms so head turns and eye movements
combine properly. Accuracy with a 640×480 webcam is a few percent of the screen width: enough
for an extended view, a streaming bubble and coarse cursor placement, not for pixel aiming.

Everything here is pure (numpy only) and deterministic; the engine feeds it frames and the GUI
drives the calibration points.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .gaze import EyeSignals
from .pose import HeadPose

FEATURE_COUNT = 19


@dataclass(frozen=True)
class GazePoint:
    """Normalised screen position: x 0..1 left→right, y 0..1 top→bottom (may fall outside 0..1)."""
    x: float
    y: float
    timestamp_nanos: int = 0

    @property
    def on_screen(self) -> bool:
        return 0.0 <= self.x <= 1.0 and 0.0 <= self.y <= 1.0


def features(eyes: EyeSignals, head: HeadPose, mirrored: bool = False) -> Optional[np.ndarray]:
    """Feature vector for one frame; None when an iris is missing (eye closed / landmarks absent)."""
    il, ir = eyes.iris_image_left, eyes.iris_image_right
    if il is None or ir is None or not head.is_finite:
        return None
    sign = -1.0 if mirrored else 1.0
    a1, d1, a2, d2 = (0.5 - il.across) * sign, il.down, (0.5 - ir.across) * sign, ir.down
    yaw, pitch, roll, x, y, z = head.yaw / 45.0, head.pitch / 45.0, head.roll / 45.0, head.x / 20.0, head.y / 20.0, head.z / 50.0
    return np.array([
        1.0, a1, d1, a2, d2, yaw, pitch, roll, x, y, z,
        a1 * yaw, a2 * yaw, d1 * pitch, d2 * pitch,
        a1 * a1, a2 * a2, d1 * d1, d2 * d2,
    ], dtype=np.float64)


@dataclass(frozen=True)
class ScreenGazeModel:
    """Ridge-regression weights (FEATURE_COUNT × 2) plus the calibration quality."""
    weights: Tuple[float, ...]           # flattened, row-major, FEATURE_COUNT*2
    rmse_x: float = float("nan")         # fraction of screen width, on the calibration points
    rmse_y: float = float("nan")
    points: int = 0
    samples: int = 0

    @property
    def valid(self) -> bool:
        return len(self.weights) == FEATURE_COUNT * 2 and all(math.isfinite(w) for w in self.weights)

    def predict(self, f: np.ndarray, timestamp_nanos: int = 0) -> Optional[GazePoint]:
        if not self.valid or f is None:
            return None
        w = np.asarray(self.weights, dtype=np.float64).reshape(FEATURE_COUNT, 2)
        out = f @ w
        if not np.all(np.isfinite(out)):
            return None
        return GazePoint(float(out[0]), float(out[1]), timestamp_nanos)

    @property
    def quality(self) -> str:
        if not self.valid or math.isnan(self.rmse_x):
            return "not calibrated"
        e = max(self.rmse_x, self.rmse_y)
        if e < 0.04:
            return "good"
        if e < 0.08:
            return "usable"
        return "rough: recalibrate with more light, sit still"


def fit(samples: Sequence[Tuple[np.ndarray, float, float]], ridge: float = 1e-4) -> Optional[ScreenGazeModel]:
    """Least squares with ridge penalty (bias term unpenalised). samples: (features, sx, sy)."""
    if len(samples) < FEATURE_COUNT:
        return None
    X = np.stack([s[0] for s in samples])
    Y = np.array([[s[1], s[2]] for s in samples], dtype=np.float64)
    reg = np.eye(FEATURE_COUNT) * ridge
    reg[0, 0] = 0.0
    try:
        W = np.linalg.solve(X.T @ X + reg * len(samples), X.T @ Y)
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(W)):
        return None
    resid = X @ W - Y
    rmse = np.sqrt(np.mean(resid ** 2, axis=0))
    return ScreenGazeModel(tuple(float(v) for v in W.reshape(-1)), float(rmse[0]), float(rmse[1]), 0, len(samples))


# --- calibration flow -------------------------------------------------------------------------

DEFAULT_POINTS: Tuple[Tuple[float, float], ...] = (
    (0.5, 0.5), (0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9),
    (0.5, 0.1), (0.9, 0.5), (0.5, 0.9), (0.1, 0.5),
)


@dataclass
class CalibrationStatus:
    index: int = 0
    total: int = len(DEFAULT_POINTS)
    point: Tuple[float, float] = (0.5, 0.5)
    phase: str = "settle"        # settle | sample | done | failed
    progress: float = 0.0        # 0..1 within the current point
    collected: int = 0
    message: str = ""


class ScreenCalibration:
    """Look at each point: `settle_seconds` to move the eyes there, `sample_seconds` of samples; then fit.

    Deterministic: driven by `update(features, now)`; the engine calls it per frame and on idle ticks.
    """

    def __init__(self, points: Sequence[Tuple[float, float]] = DEFAULT_POINTS, settle_seconds: float = 0.9,
                 sample_seconds: float = 1.2, min_samples_per_point: int = 8):
        self.points = list(points)
        self.settle = settle_seconds
        self.sample = sample_seconds
        self.min_per_point = min_samples_per_point
        self.samples: List[Tuple[np.ndarray, float, float]] = []
        self.status = CalibrationStatus(total=len(self.points), point=self.points[0], phase="settle")
        self._point_start: Optional[int] = None
        self._point_count = 0
        self.running = False
        self.result: Optional[ScreenGazeModel] = None

    def begin(self, now_nanos: int) -> None:
        self.samples.clear()
        self.status = CalibrationStatus(total=len(self.points), point=self.points[0], phase="settle")
        self._point_start = now_nanos
        self._point_count = 0
        self.running = True
        self.result = None

    def cancel(self) -> None:
        self.running = False
        self.status.phase = "failed"
        self.status.message = "Cancelled"

    def update(self, f: Optional[np.ndarray], now_nanos: int) -> CalibrationStatus:
        if not self.running or self._point_start is None:
            return self.status
        st = self.status
        t = (now_nanos - self._point_start) / 1e9
        if t < self.settle:
            st.phase = "settle"
            st.progress = t / self.settle
            return st
        st.phase = "sample"
        st.progress = min(1.0, (t - self.settle) / self.sample)
        if f is not None:
            px, py = self.points[st.index]
            self.samples.append((f, px, py))
            self._point_count += 1
            st.collected = len(self.samples)
        if t >= self.settle + self.sample:
            if self._point_count < self.min_per_point:
                self.running = False
                st.phase = "failed"
                st.message = f"Too few frames with both irises at point {st.index + 1}: face the camera with light on your face"
                return st
            st.index += 1
            self._point_count = 0
            if st.index >= len(self.points):
                self._finish()
            else:
                st.point = self.points[st.index]
                self._point_start = now_nanos
                st.progress = 0.0
        return st

    def _finish(self) -> None:
        self.running = False
        model = fit(self.samples)
        if model is None:
            self.status.phase = "failed"
            self.status.message = "The fit failed: not enough distinct samples"
            return
        self.result = replace(model, points=len(self.points))
        self.status.phase = "done"
        self.status.message = f"Calibrated: error {model.rmse_x * 100:.1f} % of width, {model.rmse_y * 100:.1f} % of height ({model.quality})"


class GazeSmoother:
    """Exponential smoothing of the gaze point with a jump pass-through: saccades are not lagged."""

    def __init__(self, tau_seconds: float = 0.12, jump: float = 0.15):
        self.tau = tau_seconds
        self.jump = jump
        self._last: Optional[GazePoint] = None

    def update(self, p: Optional[GazePoint]) -> Optional[GazePoint]:
        if p is None:
            return self._last
        if self._last is None:
            self._last = p
            return p
        dt = max(0.0, min(1.0, (p.timestamp_nanos - self._last.timestamp_nanos) / 1e9))
        if abs(p.x - self._last.x) > self.jump or abs(p.y - self._last.y) > self.jump:
            self._last = p
            return p
        a = 1.0 - math.exp(-dt / self.tau) if self.tau > 0 else 1.0
        self._last = GazePoint(self._last.x + a * (p.x - self._last.x), self._last.y + a * (p.y - self._last.y), p.timestamp_nanos)
        return self._last

    def reset(self) -> None:
        self._last = None
