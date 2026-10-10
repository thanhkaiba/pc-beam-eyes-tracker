import unittest

from headtrack_pc.anim import Eased, Transition, cycle, ease_out, pulse, road_dashes, smoothstep


class EasingTest(unittest.TestCase):
    def test_curves_are_clamped_and_hit_the_ends(self):
        for f in (smoothstep, ease_out):
            self.assertEqual(f(-1.0), 0.0)
            self.assertEqual(f(0.0), 0.0)
            self.assertEqual(f(1.0), 1.0)
            self.assertEqual(f(2.0), 1.0)
            last = 0.0
            for i in range(1, 101):
                v = f(i / 100)
                self.assertGreaterEqual(v, last)
                last = v
        self.assertGreater(ease_out(0.5), 0.5)       # fast start
        self.assertAlmostEqual(smoothstep(0.5), 0.5)

    def test_cycle_and_pulse(self):
        self.assertAlmostEqual(cycle(0.0, 0.9), 0.0)
        self.assertAlmostEqual(cycle(0.45, 0.9), 0.5)
        self.assertAlmostEqual(cycle(1.8, 0.9), 0.0)
        self.assertEqual(cycle(5.0, 0.0), 0.0)
        for t in (0.0, 0.1, 0.33, 0.5, 0.9, 7.25):
            self.assertGreaterEqual(pulse(t, 1.0), 0.0)
            self.assertLessEqual(pulse(t, 1.0), 1.0)
        self.assertAlmostEqual(pulse(0.0, 1.0), 0.0)
        self.assertAlmostEqual(pulse(0.5, 1.0), 1.0)


class EasedTest(unittest.TestCase):
    def test_converges_and_snaps(self):
        e = Eased(0.0, seconds=0.1, snap=1e-3)
        e.step(1.0, 0.1)
        self.assertAlmostEqual(e.value, 1 - 2.718281828 ** -1, places=3)   # one time constant ≈ 63 %
        for _ in range(50):
            e.step(1.0, 0.05)
        self.assertEqual(e.value, 1.0)       # snapped exactly
        self.assertEqual(e.jump(-2.0), -2.0)
        self.assertEqual(e.step(-2.0, 0.0), -2.0)

    def test_frame_rate_independent(self):
        slow, fast = Eased(0.0, seconds=0.2, snap=0.0), Eased(0.0, seconds=0.2, snap=0.0)
        for _ in range(10):
            slow.step(10.0, 0.05)            # 20 fps for 0.5 s
        for _ in range(100):
            fast.step(10.0, 0.005)           # 200 fps for 0.5 s
        self.assertAlmostEqual(slow.value, fast.value, places=6)
        self.assertGreater(slow.value, 9.0)  # 2.5 time constants ≈ 92 %

    def test_zero_dt_is_a_no_op(self):
        e = Eased(0.0, seconds=0.1, snap=1e-3)
        self.assertEqual(e.step(5.0, 0.0), 0.0)
        self.assertEqual(e.step(5.0, -1.0), 0.0)


class TransitionTest(unittest.TestCase):
    def test_lifecycle(self):
        t = Transition(0.25)
        self.assertFalse(t.started)
        self.assertFalse(t.active(10.0))
        self.assertEqual(t.progress(10.0), 0.0)
        t.start(10.0)
        self.assertTrue(t.active(10.0))
        self.assertTrue(t.active(10.2))
        self.assertFalse(t.active(10.25))
        self.assertTrue(t.finished(10.25))
        self.assertEqual(t.progress(10.0), 0.0)
        self.assertGreater(t.progress(10.1), 0.0)
        self.assertLess(t.progress(10.1), 1.0)
        self.assertEqual(t.progress(11.0), 1.0)
        t.reset()
        self.assertFalse(t.started)
        self.assertEqual(t.progress(11.0), 0.0)


class RoadTest(unittest.TestCase):
    def test_dashes_stay_in_range_and_scroll(self):
        for phase in (0.0, 0.25, 0.5, 0.99):
            dashes = list(road_dashes(phase))
            self.assertGreaterEqual(len(dashes), 5)
            for t0, t1 in dashes:
                self.assertGreater(t1, t0)
                self.assertGreaterEqual(t0, 0.02)
                self.assertLessEqual(t1, 1.0)
        a, b = list(road_dashes(0.0)), list(road_dashes(0.5))
        self.assertNotEqual(a, b)
        # dashes travel toward the viewer (t grows) as the phase advances
        self.assertGreater(b[1][0], a[1][0])
        # the end of one period lines up with the start of the next (seamless loop)
        end = list(road_dashes(1.0 - 1e-9))
        for (a0, a1), (e0, e1) in zip(a[1:], end):
            self.assertAlmostEqual(a0, e0, places=6)
            self.assertAlmostEqual(a1, e1, places=6)

if __name__ == "__main__":
    unittest.main()
