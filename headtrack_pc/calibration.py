"""Neutral-pose calibration with stability checks (port of core `calibration`)."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import List, Optional

from .pose import HeadPose, wrap_degrees


class CalibrationFailure(Enum):
    NO_FACE = "No face was tracked during the sampling window. Face the camera and try again."
    TOO_FEW_SAMPLES = "Not enough valid tracking samples. Keep your face in view for the whole countdown."
    UNSTABLE = "Head was moving during sampling. Hold still, look at the screen, and try again."
    LOW_CONFIDENCE = "Tracking confidence was too low. Improve lighting or move closer to the camera."

    @property
    def message(self) -> str:
        return self.value


@dataclass(frozen=True)
class CalibrationStats:
    samples: int
    rejected: int
    max_angular_spread: float
    angular_std_dev: float
    translation_std_dev: float
    duration_millis: int


@dataclass(frozen=True)
class CalibrationSettings:
    countdown_millis: int = 3000
    sample_window_millis: int = 1000
    min_samples: int = 10
    max_angular_std_dev: float = 1.5
    max_angular_spread: float = 4.0
    max_translation_std_dev: float = 1.0
    min_confidence: float = 0.5
    min_valid_fraction: float = 0.7


@dataclass(frozen=True)
class CalibrationResult:
    neutral: Optional[HeadPose]
    failure: Optional[CalibrationFailure]
    stats: Optional[CalibrationStats]

    @property
    def ok(self) -> bool:
        return self.failure is None and self.neutral is not None


def mean_pose(poses: List[HeadPose]) -> HeadPose:
    """Component-wise mean; angles averaged relative to the first sample to handle wrap-around."""
    if not poses:
        raise ValueError("empty")
    ref = poses[0]
    acc = [0.0] * 6
    for p in poses:
        acc[0] += wrap_degrees(p.yaw - ref.yaw)
        acc[1] += wrap_degrees(p.pitch - ref.pitch)
        acc[2] += wrap_degrees(p.roll - ref.roll)
        acc[3] += p.x
        acc[4] += p.y
        acc[5] += p.z
    n = float(len(poses))
    return HeadPose(wrap_degrees(ref.yaw + acc[0] / n), wrap_degrees(ref.pitch + acc[1] / n),
                    wrap_degrees(ref.roll + acc[2] / n), acc[3] / n, acc[4] / n, acc[5] / n)


class NeutralCalibrator:
    def __init__(self, settings: CalibrationSettings = CalibrationSettings()):
        self.settings = settings
        self.samples: List[HeadPose] = []
        self.rejected = 0
        self.low_confidence = 0
        self.start_nanos = 0
        self.last_nanos = 0

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    def begin(self, now_nanos: int) -> None:
        self.samples.clear()
        self.rejected = 0
        self.low_confidence = 0
        self.start_nanos = self.last_nanos = now_nanos

    def add_sample(self, pose: Optional[HeadPose], now_nanos: Optional[int] = None) -> None:
        if now_nanos is None:
            now_nanos = pose.timestamp_nanos if pose is not None else self.last_nanos
        self.last_nanos = max(self.last_nanos, now_nanos)
        if pose is None or not pose.is_finite:
            self.rejected += 1
            return
        if not math.isnan(pose.confidence) and pose.confidence < self.settings.min_confidence:
            self.rejected += 1
            self.low_confidence += 1
            return
        self.samples.append(pose)

    def window_elapsed(self, now_nanos: int) -> bool:
        return (now_nanos - self.start_nanos) // 1_000_000 >= self.settings.sample_window_millis

    def finish(self) -> CalibrationResult:
        s = self.settings
        total = len(self.samples) + self.rejected
        duration = (self.last_nanos - self.start_nanos) // 1_000_000
        if not self.samples:
            reason = CalibrationFailure.LOW_CONFIDENCE if (self.low_confidence > 0 and self.low_confidence == self.rejected) else CalibrationFailure.NO_FACE
            return CalibrationResult(None, reason, CalibrationStats(0, self.rejected, 0.0, 0.0, 0.0, duration))
        mean = mean_pose(self.samples)
        max_spread = 0.0
        variance = [0.0] * 6
        for p in self.samples:
            arr = (p - mean).to_array()
            for i in range(3):
                max_spread = max(max_spread, abs(arr[i]))
            for i in range(6):
                variance[i] += arr[i] * arr[i]
        n = len(self.samples)
        variance = [v / n for v in variance]
        ang_std = math.sqrt(max(variance[0], variance[1], variance[2]))
        tr_std = math.sqrt(max(variance[3], variance[4], variance[5]))
        stats = CalibrationStats(n, self.rejected, max_spread, ang_std, tr_std, duration)
        if n < s.min_samples or n / total < s.min_valid_fraction:
            reason = CalibrationFailure.LOW_CONFIDENCE if (self.low_confidence > 0 and self.low_confidence >= self.rejected / 2) else CalibrationFailure.TOO_FEW_SAMPLES
            return CalibrationResult(None, reason, stats)
        if ang_std > s.max_angular_std_dev or max_spread > s.max_angular_spread or tr_std > s.max_translation_std_dev:
            return CalibrationResult(None, CalibrationFailure.UNSTABLE, stats)
        return CalibrationResult(replace(mean, timestamp_nanos=self.last_nanos, confidence=math.nan), None, stats)
