import math
import unittest
from dataclasses import replace

from headtrack_pc import pose as posemath
from headtrack_pc.calibration import CalibrationFailure, CalibrationSettings, NeutralCalibrator
from headtrack_pc.faceloss import FaceLossHandler, FaceLossSettings, TrackingState
from headtrack_pc.filters import FilterType, SmoothingSettings
from headtrack_pc.pipeline import TrackingPipeline
from headtrack_pc.pose import HeadPose
from headtrack_pc.profile import DRIVING, PASSTHROUGH

MS = 1_000_000


class CalibratorTest(unittest.TestCase):
    def test_stable_samples_succeed(self):
        c = NeutralCalibrator(CalibrationSettings(min_samples=5))
        c.begin(0)
        for i in range(20):
            c.add_sample(HeadPose(10.0 + 0.1 * (i % 2), -20.0, 1.0, 0, 0, -40, timestamp_nanos=i * 50 * MS))
        r = c.finish()
        self.assertTrue(r.ok)
        self.assertAlmostEqual(r.neutral.yaw, 10.05, delta=0.01)
        self.assertEqual(r.stats.samples, 20)

    def test_no_face_and_unstable(self):
        c = NeutralCalibrator()
        c.begin(0)
        for i in range(10):
            c.add_sample(None, i * 50 * MS)
        self.assertEqual(c.finish().failure, CalibrationFailure.NO_FACE)
        c.begin(0)
        for i in range(20):
            c.add_sample(HeadPose(i * 2.0, 0, 0, timestamp_nanos=i * 50 * MS))
        self.assertEqual(c.finish().failure, CalibrationFailure.UNSTABLE)

    def test_low_confidence_and_too_few(self):
        c = NeutralCalibrator()
        c.begin(0)
        for i in range(10):
            c.add_sample(HeadPose(0, 0, 0, confidence=0.1, timestamp_nanos=i * MS))
        self.assertEqual(c.finish().failure, CalibrationFailure.LOW_CONFIDENCE)
        c.begin(0)
        for i in range(3):
            c.add_sample(HeadPose(0, 0, 0, timestamp_nanos=i * MS))
        self.assertEqual(c.finish().failure, CalibrationFailure.TOO_FEW_SAMPLES)
        self.assertTrue(c.window_elapsed(1_100 * MS))

    def test_mean_wraps(self):
        c = NeutralCalibrator(CalibrationSettings(min_samples=2))
        c.begin(0)
        c.add_sample(HeadPose(179.0, 0, 0, timestamp_nanos=1))
        c.add_sample(HeadPose(-179.0, 0, 0, timestamp_nanos=2))
        r = c.finish()
        self.assertTrue(r.ok)
        self.assertAlmostEqual(abs(r.neutral.yaw), 180.0, delta=0.01)


class FaceLossTest(unittest.TestCase):
    def test_hold_return_neutral(self):
        h = FaceLossHandler(FaceLossSettings(300, 600))
        out = h.update(HeadPose(20, 0, 0), 0)
        self.assertEqual(h.state, TrackingState.TRACKING)
        self.assertEqual(h.update(None, 0).yaw, 20.0)  # lost now
        self.assertEqual(h.update(None, 100 * MS).yaw, 20.0)
        self.assertEqual(h.state, TrackingState.HOLDING)
        mid = h.update(None, 600 * MS)
        self.assertEqual(h.state, TrackingState.RETURNING)
        self.assertAlmostEqual(mid.yaw, 10.0, delta=0.5)
        self.assertEqual(h.update(None, 1000 * MS).yaw, 0.0)
        self.assertEqual(h.state, TrackingState.NEUTRAL)
        h.update_settings(FaceLossSettings(0, 0))
        self.assertEqual(h.update(None, 2000 * MS).yaw, 0.0)
        with self.assertRaises(ValueError):
            FaceLossSettings(-1, 0)


class PipelineTest(unittest.TestCase):
    def frames(self, pipeline, raw, t0, n=25, step=40 * MS):
        snap = None
        for i in range(n):
            snap = pipeline.process(raw, t0 + i * step)
        return snap

    def calibrate(self, pipeline, neutral, t0=0):
        pipeline.begin_calibration(t0)
        for i in range(30):
            pipeline.process(replace(neutral, timestamp_nanos=t0 + i * 40 * MS), t0 + i * 40 * MS)
        self.assertTrue(pipeline.calibration_window_elapsed(t0 + 30 * 40 * MS))
        r = pipeline.finish_calibration()
        self.assertTrue(r.ok, r.failure)

    def test_uncalibrated_outputs_neutral_but_shows_raw(self):
        p = TrackingPipeline(PASSTHROUGH)
        s = p.process(HeadPose(10, 5, 1), 0)
        self.assertFalse(s.is_calibrated)
        self.assertEqual(s.output.yaw, 0.0)
        self.assertEqual(s.raw.yaw, 10.0)

    def test_calibrated_passthrough_maps_relative(self):
        p = TrackingPipeline(replace(PASSTHROUGH, smoothing=SmoothingSettings(type=FilterType.NONE)))
        neutral = HeadPose(5.0, -30.0, 2.0, 0, 0, -40)
        self.calibrate(p, neutral)
        raw = posemath.apply_relative(neutral, HeadPose(20.0, 0.0, 0.0))
        s = p.process(raw, 10_000 * MS)
        self.assertAlmostEqual(s.calibrated.yaw, 20.0, delta=1e-3)
        self.assertAlmostEqual(s.output.yaw, (20.0 - 1.0) * (90.0 / 89.0), delta=1e-3)  # dead zone 1° re-scaled
        self.assertEqual(s.state, TrackingState.TRACKING)

    def test_driving_profile_triples_yaw(self):
        p = TrackingPipeline(replace(DRIVING, smoothing=SmoothingSettings(type=FilterType.NONE)))
        self.calibrate(p, HeadPose(0, 0, 0, 0, 0, -40))
        s = p.process(HeadPose(30.0, 0, 0, 0, 0, -40), 10_000 * MS)
        self.assertGreater(s.output.yaw, 80.0)
        self.assertLessEqual(s.output.yaw, 90.0)

    def test_face_loss_eases_and_recenter(self):
        p = TrackingPipeline(replace(PASSTHROUGH, smoothing=SmoothingSettings(type=FilterType.NONE)))
        self.calibrate(p, HeadPose(0, 0, 0, 0, 0, -40))
        t = 10_000 * MS
        p.process(HeadPose(20, 0, 0, 0, 0, -40), t)
        held = p.process(None, t + 100 * MS)
        self.assertEqual(held.state, TrackingState.HOLDING)
        self.assertGreater(held.output.yaw, 18.0)
        gone = p.tick(t + 2000 * MS)
        self.assertEqual(gone.yaw, 0.0)
        self.assertTrue(p.recenter())  # last raw was yaw 20 → now neutral
        s = p.process(HeadPose(20, 0, 0, 0, 0, -40), t + 3000 * MS)
        self.assertAlmostEqual(s.calibrated.yaw, 0.0, delta=1e-6)
        p.clear_calibration()
        self.assertFalse(p.is_calibrated)
        self.assertFalse(TrackingPipeline(PASSTHROUGH).recenter())

    def test_update_profile_keeps_neutral(self):
        p = TrackingPipeline(PASSTHROUGH)
        self.calibrate(p, HeadPose(3, 3, 3, 0, 0, -40))
        n = p.neutral
        p.update_profile(replace(DRIVING, neutral_pose=None))
        self.assertEqual(p.neutral, n)
        self.assertEqual(p.profile.neutral_pose, n)
        self.assertEqual(p.profile.mapping, DRIVING.mapping)

    def test_finish_without_begin_fails(self):
        p = TrackingPipeline(PASSTHROUGH)
        self.assertFalse(p.finish_calibration().ok)
        p.begin_calibration(0)
        self.assertTrue(p.is_calibrating)
        p.cancel_calibration()
        self.assertFalse(p.is_calibrating)
