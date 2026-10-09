import unittest

from headtrack_pc.filters import ExponentialFilter, FilterType, OneEuroFilter, PoseFilter, SmoothingSettings
from headtrack_pc.mapping import AxisSettings, MappingSettings, ResponseCurve, map_axis
from headtrack_pc.pose import Axis, HeadPose


class MappingTest(unittest.TestCase):
    def test_dead_zone_continuous(self):
        s = AxisSettings(dead_zone=2.0, max_output=90.0)
        self.assertEqual(map_axis(1.9, s), 0.0)
        self.assertLess(map_axis(2.01, s), 0.05)
        self.assertAlmostEqual(map_axis(90.0, s), 90.0)
        self.assertAlmostEqual(map_axis(-90.0, s), -90.0)

    def test_sensitivity_curve_clamp_invert(self):
        s = AxisSettings(sensitivity=2.0, max_output=90.0)
        self.assertAlmostEqual(map_axis(30.0, s), 60.0)
        self.assertAlmostEqual(map_axis(80.0, s), 90.0)
        self.assertAlmostEqual(map_axis(-80.0, AxisSettings(sensitivity=2.0, max_output=90.0, inverted=True)), 90.0)
        sq = AxisSettings(curve=ResponseCurve.SQUARED, max_output=100.0)
        self.assertAlmostEqual(map_axis(50.0, sq), 25.0)
        self.assertAlmostEqual(map_axis(-50.0, sq), -25.0)
        self.assertAlmostEqual(map_axis(100.0, AxisSettings(curve=ResponseCurve.CUBIC, max_output=100.0)), 100.0)
        self.assertEqual(map_axis(float("nan"), s), 0.0)
        self.assertEqual(map_axis(50.0, AxisSettings(enabled=False)), 0.0)

    def test_curves_are_odd_and_fix_one(self):
        for c in ResponseCurve:
            self.assertAlmostEqual(c.apply(1.0), 1.0)
            self.assertAlmostEqual(c.apply(-0.3), -c.apply(0.3))
            self.assertEqual(c.apply(float("inf")), 0.0)

    def test_axes_independent(self):
        m = MappingSettings().with_axis(Axis.YAW, AxisSettings(sensitivity=3.0))
        p = m.map(HeadPose(10.0, 10.0, 10.0))
        self.assertAlmostEqual(p.yaw, 30.0)
        self.assertAlmostEqual(p.pitch, map_axis(10.0, m.pitch))
        self.assertAlmostEqual(p.pitch, 9.1011, places=3)
        self.assertEqual(m.get(Axis.PITCH), m.pitch)

    def test_settings_validation(self):
        with self.assertRaises(ValueError):
            AxisSettings(sensitivity=-1.0)
        with self.assertRaises(ValueError):
            AxisSettings(max_output=0.0)


class FilterTest(unittest.TestCase):
    def test_exponential_converges(self):
        f = ExponentialFilter(0.1)
        t = 0
        v = f.filter(0.0, t)
        for _ in range(100):
            t += 10_000_000
            v = f.filter(10.0, t)
        self.assertAlmostEqual(v, 10.0, delta=0.01)

    def test_one_euro_smooths_and_follows(self):
        f = OneEuroFilter(min_cutoff=1.0, beta=0.0)
        t = 0
        f.filter(0.0, t)
        t += 16_000_000
        first = f.filter(10.0, t)
        self.assertLess(first, 10.0)
        for _ in range(300):
            t += 16_000_000
            v = f.filter(10.0, t)
        self.assertAlmostEqual(v, 10.0, delta=0.05)

    def test_non_finite_holds_last(self):
        f = OneEuroFilter()
        self.assertEqual(f.filter(float("nan"), 0), 0.0)
        f.filter(5.0, 1)
        self.assertEqual(f.filter(float("nan"), 2), 5.0)

    def test_settings_map_strength(self):
        s = SmoothingSettings(strength=0.0)
        self.assertAlmostEqual(s.one_euro_min_cutoff, 10.0)
        self.assertEqual(type(s.new_filter()).__name__, "PassThroughFilter")
        self.assertEqual(type(SmoothingSettings(type=FilterType.NONE, strength=1.0).new_filter()).__name__, "PassThroughFilter")
        self.assertEqual(type(SmoothingSettings(type=FilterType.EXPONENTIAL).new_filter()).__name__, "ExponentialFilter")
        with self.assertRaises(ValueError):
            SmoothingSettings(strength=1.5)
        pf = PoseFilter(SmoothingSettings(strength=0.0))
        self.assertEqual(pf.filter(HeadPose(1, 2, 3, timestamp_nanos=5)).yaw, 1.0)
