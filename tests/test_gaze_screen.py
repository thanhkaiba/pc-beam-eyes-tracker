import math
import unittest

import numpy as np

from headtrack_pc import gaze_screen as gs
from headtrack_pc.gaze import EyeSignals, IrisPosition
from headtrack_pc.pose import HeadPose


def synth_eyes(sx: float, sy: float, head: HeadPose, noise=0.0, rng=None):
    """A fake person: iris moves linearly with the screen point and counter to head yaw/pitch."""
    yaw_c, pitch_c = head.yaw / 45.0, head.pitch / 45.0
    across = 0.5 - (sx - 0.5) * 0.3 + yaw_c * 0.12   # looking right (sx>0.5) → iris toward image-left (smaller across)
    down = (sy - 0.5) * 0.12 - pitch_c * 0.05
    if rng is not None and noise:
        across += rng.normal(0, noise)
        down += rng.normal(0, noise * 0.4)
    return EyeSignals(iris_image_left=IrisPosition(across, down), iris_image_right=IrisPosition(across + 0.02, down))


class FeatureTest(unittest.TestCase):
    def test_features_shape_and_missing_iris(self):
        f = gs.features(synth_eyes(0.5, 0.5, HeadPose(0, 0, 0)), HeadPose(0, 0, 0, 0, 0, -45))
        self.assertEqual(f.shape, (gs.FEATURE_COUNT,))
        self.assertEqual(f[0], 1.0)
        self.assertIsNone(gs.features(EyeSignals(), HeadPose(0, 0, 0)))
        self.assertIsNone(gs.features(synth_eyes(0.5, 0.5, HeadPose(0, 0, 0)), HeadPose(float("nan"), 0, 0)))
        m = gs.features(synth_eyes(0.5, 0.5, HeadPose(0, 0, 0)), HeadPose(0, 0, 0), mirrored=True)
        self.assertAlmostEqual(m[1], -f[1])


class FitTest(unittest.TestCase):
    def test_fit_recovers_synthetic_gaze(self):
        rng = np.random.default_rng(1)
        samples = []
        for px, py in gs.DEFAULT_POINTS:
            for _ in range(30):
                head = HeadPose(rng.normal(0, 3), rng.normal(0, 2), 0, 0, 0, -50)
                f = gs.features(synth_eyes(px, py, head, 0.004, rng), head)
                samples.append((f, px, py))
        model = gs.fit(samples)
        self.assertIsNotNone(model)
        self.assertTrue(model.valid)
        self.assertLess(model.rmse_x, 0.05)
        self.assertLess(model.rmse_y, 0.05)
        self.assertIn(model.quality, ("good", "usable"))
        head = HeadPose(2.0, -1.0, 0, 0, 0, -50)
        p = model.predict(gs.features(synth_eyes(0.8, 0.3, head), head), 5)
        self.assertAlmostEqual(p.x, 0.8, delta=0.08)
        self.assertAlmostEqual(p.y, 0.3, delta=0.08)
        self.assertEqual(p.timestamp_nanos, 5)
        self.assertTrue(p.on_screen)
        self.assertFalse(gs.GazePoint(1.2, 0.5).on_screen)

    def test_fit_rejects_too_few(self):
        self.assertIsNone(gs.fit([]))
        bad = gs.ScreenGazeModel(weights=(1.0,) * 3)
        self.assertFalse(bad.valid)
        self.assertIsNone(bad.predict(np.ones(gs.FEATURE_COUNT)))
        self.assertEqual(bad.quality, "not calibrated")


class CalibrationFlowTest(unittest.TestCase):
    def run_flow(self, feature_for_point, frames_per_second=30):
        cal = gs.ScreenCalibration(settle_seconds=0.5, sample_seconds=0.5, min_samples_per_point=5)
        t = 0
        cal.begin(t)
        statuses = []
        for _ in range(int((0.5 + 0.5) * len(gs.DEFAULT_POINTS) * frames_per_second) + 60):
            t += int(1e9 / frames_per_second)
            px, py = cal.status.point
            f = feature_for_point(px, py)
            statuses.append(cal.update(f, t).phase)
            if not cal.running:
                break
        return cal, statuses

    def test_full_flow_produces_model(self):
        rng = np.random.default_rng(2)

        def feat(px, py):
            head = HeadPose(rng.normal(0, 2), rng.normal(0, 2), 0, 0, 0, -50)
            return gs.features(synth_eyes(px, py, head, 0.003, rng), head)
        cal, phases = self.run_flow(feat)
        self.assertEqual(cal.status.phase, "done", cal.status.message)
        self.assertIsNotNone(cal.result)
        self.assertEqual(cal.result.points, len(gs.DEFAULT_POINTS))
        self.assertIn("settle", phases)
        self.assertIn("sample", phases)
        self.assertIn("Calibrated", cal.status.message)
        self.assertFalse(cal.running)

    def test_missing_irises_fail_with_message(self):
        cal, _ = self.run_flow(lambda px, py: None)
        self.assertEqual(cal.status.phase, "failed")
        self.assertIn("Too few frames", cal.status.message)
        cal.begin(0)
        cal.cancel()
        self.assertEqual(cal.status.phase, "failed")
        self.assertEqual(cal.update(None, 10).phase, "failed")


class SmootherTest(unittest.TestCase):
    def test_smooths_small_moves_and_passes_saccades(self):
        s = gs.GazeSmoother(tau_seconds=0.1, jump=0.15)
        self.assertEqual(s.update(gs.GazePoint(0.5, 0.5, 0)).x, 0.5)
        mid = s.update(gs.GazePoint(0.55, 0.5, 50_000_000))
        self.assertTrue(0.5 < mid.x < 0.55)
        far = s.update(gs.GazePoint(0.9, 0.5, 100_000_000))
        self.assertEqual(far.x, 0.9)
        self.assertEqual(s.update(None).x, 0.9)
        s.reset()
        self.assertIsNone(s.update(None))
