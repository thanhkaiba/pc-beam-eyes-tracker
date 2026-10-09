import unittest

from headtrack_pc.outputs.mouse import HeadMouseMapper, MouseBackend, MouseMode, MouseOutput, MouseSettings
from headtrack_pc.pose import HeadPose


class FakeBackend(MouseBackend):
    def __init__(self):
        self.rel = []
        self.abs = []

    def move_relative(self, dx, dy):
        self.rel.append((dx, dy))

    def move_absolute(self, x, y):
        self.abs.append((x, y))

    def screen_size(self):
        return (1920, 1080)


class HeadMouseTest(unittest.TestCase):
    def test_relative_deltas_accumulate_fractions(self):
        m = HeadMouseMapper(MouseSettings(mode=MouseMode.HEAD, pixels_per_degree_x=10, pixels_per_degree_y=10, dead_zone_degrees=0.0))
        self.assertEqual(m.delta(HeadPose(0, 0, 0)), (0, 0))
        self.assertEqual(m.delta(HeadPose(1.0, 0, 0)), (10, 0))
        self.assertEqual(m.delta(HeadPose(1.0, 1.0, 0)), (0, -10))  # look up → mouse up
        total = 0
        for i in range(10):
            dx, _ = m.delta(HeadPose(1.0 + 0.05 * (i + 1), 1.0, 0))
            total += dx
        self.assertEqual(total, 5)  # 0.5° × 10 px, no rounding loss
        self.assertEqual(m.delta(None), (0, 0))
        self.assertEqual(m.delta(HeadPose(50, 0, 0)), (0, 0))  # first pose after a loss: no jump
        inv = HeadMouseMapper(MouseSettings(mode=MouseMode.HEAD, pixels_per_degree_y=10, invert_y=True, dead_zone_degrees=0.0))
        inv.delta(HeadPose(0, 0, 0))
        self.assertEqual(inv.delta(HeadPose(0, 1.0, 0)), (0, 10))
        dz = HeadMouseMapper(MouseSettings(mode=MouseMode.HEAD, dead_zone_degrees=0.5))
        dz.delta(HeadPose(0, 0, 0))
        self.assertEqual(dz.delta(HeadPose(0.2, 0, 0)), (0, 0))
        with self.assertRaises(ValueError):
            MouseSettings(pixels_per_degree_x=-1)


class MouseOutputTest(unittest.TestCase):
    def test_head_mode_moves_only_when_active(self):
        b = FakeBackend()
        out = MouseOutput(MouseSettings(mode=MouseMode.HEAD, pixels_per_degree_x=10, dead_zone_degrees=0), b)
        out.write(HeadPose(0, 0, 0))
        out.write(HeadPose(2, 0, 0))
        self.assertEqual(b.rel, [])  # not active yet
        out.active = True
        out.write(HeadPose(2, 0, 0))
        out.write(HeadPose(4, 0, 0))
        self.assertEqual(b.rel, [(20, 0)])

    def test_gaze_modes(self):
        b = FakeBackend()
        out = MouseOutput(MouseSettings(mode=MouseMode.GAZE_FOLLOW, gaze_smoothing=0.0), b)
        out.active = True
        out.gaze(0.5, 0.5)
        self.assertEqual(b.abs, [(959, 539)])
        out.gaze(None, None)
        self.assertEqual(len(b.abs), 1)
        out.gaze(2.0, -1.0)
        self.assertEqual(b.abs[-1], (1919, 0))  # clamped
        hot = MouseOutput(MouseSettings(mode=MouseMode.GAZE_HOTKEY, gaze_smoothing=0.0), b)
        hot.gaze(0.1, 0.1)
        self.assertEqual(len(b.abs), 2)  # no warp without the hotkey
        hot.warp()
        hot.gaze(0.1, 0.1)
        self.assertEqual(b.abs[-1], (191, 107))
        hot.gaze(0.2, 0.2)
        self.assertEqual(len(b.abs), 3)  # one warp per press
        smooth = MouseOutput(MouseSettings(mode=MouseMode.GAZE_FOLLOW, gaze_smoothing=0.5), b)
        smooth.active = True
        smooth.gaze(0.0, 0.0)
        smooth.gaze(1.0, 1.0)
        self.assertEqual(b.abs[-1], (959, 539))
        off = MouseOutput(MouseSettings(), b)
        off.write(HeadPose(1, 1, 1))
        off.close()
