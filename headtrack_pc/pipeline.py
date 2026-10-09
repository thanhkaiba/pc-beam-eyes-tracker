"""raw → calibrated → mapped → filtered → face-loss policy → output (port of core `pipeline`)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from . import pose as posemath
from .calibration import CalibrationFailure, CalibrationResult, NeutralCalibrator
from .faceloss import FaceLossHandler, TrackingState
from .filters import PoseFilter
from .pose import NEUTRAL, HeadPose
from .profile import TrackingProfile


@dataclass(frozen=True)
class PipelineSnapshot:
    raw: Optional[HeadPose]
    calibrated: Optional[HeadPose]
    filtered: Optional[HeadPose]
    output: HeadPose
    state: TrackingState
    has_neutral: bool

    @property
    def is_calibrated(self) -> bool:
        return self.has_neutral


class TrackingPipeline:
    """Deterministic, single-threaded. The neutral pose only changes by calibrate / recenter / clear."""

    def __init__(self, profile: TrackingProfile):
        self.profile = profile
        self.neutral: Optional[HeadPose] = profile.neutral_pose
        self._filter = PoseFilter(profile.smoothing)
        self._face_loss = FaceLossHandler(profile.face_loss)
        self._calibrator: Optional[NeutralCalibrator] = None
        self._last_raw: Optional[HeadPose] = None

    @property
    def is_calibrating(self) -> bool:
        return self._calibrator is not None

    @property
    def calibration_sample_count(self) -> int:
        return self._calibrator.sample_count if self._calibrator else 0

    @property
    def is_calibrated(self) -> bool:
        return self.neutral is not None

    @property
    def state(self) -> TrackingState:
        return self._face_loss.state

    def update_profile(self, p: TrackingProfile) -> None:
        smoothing_changed = p.smoothing != self.profile.smoothing
        face_loss_changed = p.face_loss != self.profile.face_loss
        self.profile = replace(p, neutral_pose=self.neutral)
        if smoothing_changed:
            self._filter = PoseFilter(p.smoothing)
        if face_loss_changed:
            self._face_loss.update_settings(p.face_loss)

    def process(self, raw: Optional[HeadPose], now_nanos: int) -> PipelineSnapshot:
        valid = raw is not None and raw.is_finite
        if valid:
            self._last_raw = raw
        if self._calibrator is not None:
            self._calibrator.add_sample(raw if valid else None, now_nanos)
        n = self.neutral
        if not valid or n is None:
            out = self._face_loss.update(None, now_nanos) if not valid else NEUTRAL.with_time(now_nanos)
            return PipelineSnapshot(raw if valid else None, None, None, out,
                                    TrackingState.TRACKING if valid else self._face_loss.state, n is not None)
        calibrated = posemath.relative_to(raw, n)
        mapped = self.profile.mapping.map(calibrated)
        filtered = self._filter.filter(mapped)
        output = self._face_loss.update(filtered, now_nanos)
        return PipelineSnapshot(raw, calibrated, filtered, output, TrackingState.TRACKING, True)

    def tick(self, now_nanos: int) -> HeadPose:
        return self._face_loss.update(None, now_nanos)

    # --- calibration -----------------------------------------------------------------
    def begin_calibration(self, now_nanos: int) -> None:
        self._calibrator = NeutralCalibrator(self.profile.calibration)
        self._calibrator.begin(now_nanos)

    def calibration_window_elapsed(self, now_nanos: int) -> bool:
        return self._calibrator.window_elapsed(now_nanos) if self._calibrator else False

    def finish_calibration(self) -> CalibrationResult:
        c = self._calibrator
        if c is None:
            return CalibrationResult(None, CalibrationFailure.NO_FACE, None)
        self._calibrator = None
        r = c.finish()
        if r.ok:
            self.apply_calibration(r.neutral)
        return r

    def cancel_calibration(self) -> None:
        self._calibrator = None

    def apply_calibration(self, new_neutral: HeadPose) -> None:
        self.neutral = new_neutral
        self.profile = replace(self.profile, neutral_pose=new_neutral)
        self._filter.reset()
        self._face_loss.reset()

    def drift_neutral(self, new_neutral: HeadPose) -> None:
        """Moves the centre without resetting filters or the face-loss state (automatic centre correction)."""
        self.neutral = new_neutral
        self.profile = replace(self.profile, neutral_pose=new_neutral)

    def recenter(self) -> bool:
        r = self._last_raw
        if r is None:
            return False
        self.apply_calibration(replace(r, timestamp_nanos=0, confidence=float("nan")))
        return True

    def clear_calibration(self) -> None:
        self.neutral = None
        self.profile = replace(self.profile, neutral_pose=None)
        self._filter.reset()
        self._face_loss.reset()
