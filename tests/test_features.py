import math
import os
import tempfile
import time
import unittest
from dataclasses import replace

from headtrack_pc import pose as posemath
from headtrack_pc import profiles, selfcheck, sim
from headtrack_pc.autocentre import AutoCentre, AutoCentreSettings
from headtrack_pc.gaze import (CompensationFailure, EyeAssist, EyeAssistSettings, EyeSignals, GazeCompensationCalibrator,
                               GazeSource, IrisPosition, Point2, estimate, iris_position)
from headtrack_pc.hotkeys import EdgeDetector, HotkeyPoller, HotkeySettings, JoystickReader, KeyReader, parse_key
from headtrack_pc.pose import NEUTRAL, Axis, HeadPose
from headtrack_pc.profile import DRIVING, PASSTHROUGH, OutputSettings, TrackingProfile
from headtrack_pc.selfcheck import CheckResult

MS = 1_000_000


class SweepTest(unittest.TestCase):
    def test_phases_cover_all_six_directions_and_end(self):
        axes = [(p.axis, p.delta > 0) for p in sim.PHASES if p.axis is not None]
        self.assertEqual(axes, [(Axis.YAW, True), (Axis.YAW, False), (Axis.PITCH, True), (Axis.PITCH, False), (Axis.ROLL, True), (Axis.ROLL, False)])
        self.assertIsNone(sim.pose_at(sim.TOTAL_SECONDS + 0.1, NEUTRAL))
        self.assertIsNone(sim.position_at(-1))
        self.assertEqual(sim.progress(sim.TOTAL_SECONDS * 2), 1.0)

    def test_poses_are_eased_and_built_on_the_centre(self):
        centre = HeadPose(5.0, -20.0, 1.0, 0, 0, -45)
        start = sim.pose_at(0.0, centre)
        self.assertEqual(start.to_array(), centre.to_array())
        t_mid = sim.CENTRE_SECONDS + sim.MOVE_SECONDS / 2
        mid = sim.pose_at(t_mid, centre)
        self.assertAlmostEqual(mid.yaw, centre.yaw + 25.0, delta=1e-6)
        self.assertEqual(mid.pitch, centre.pitch)
        edge = sim.pose_at(sim.CENTRE_SECONDS + 0.1, centre)
        self.assertLess(abs(edge.yaw - centre.yaw), 1.5)  # ramping in (smoothstep), no jump
        self.assertEqual(sim.envelope(0.0, 2.5), 0.0)
        self.assertEqual(sim.envelope(1.25, 2.5), 1.0)
        pos = sim.position_at(t_mid)
        self.assertEqual(pos.phase.label, "Yaw right")
        self.assertAlmostEqual(pos.phase_progress, 0.5)


class CockpitTest(unittest.TestCase):
    def test_camera_signs(self):
        c = sim.camera(HeadPose(45.0, 0, 0))
        self.assertAlmostEqual(c.pan_x, -0.5)
        self.assertIn("right mirror", c.words)
        self.assertGreater(sim.camera(HeadPose(0, 30.0, 0)).pan_y, 0)
        self.assertEqual(sim.camera(HeadPose(0, 0, 10.0)).roll_degrees, -10.0)
        self.assertIn("road ahead", sim.camera(HeadPose(0, 0, 0)).words)
        self.assertIn("nothing sent", sim.camera(None).words)
        self.assertEqual(sim.camera(HeadPose(0, 0, 0, z=100)).zoom, 1.5)
        self.assertIn("left window", sim.camera(HeadPose(-80, 0, 0)).words)


class GazeTest(unittest.TestCase):
    def test_estimate_signs_and_mirroring(self):
        right = EyeSignals(look_in_left=0.8, look_out_right=0.8)
        r = estimate(right, mirrored=False, timestamp_nanos=1)
        self.assertGreater(r.blend_horizontal, 0.7)
        self.assertTrue(r.eyes_open)
        self.assertLess(estimate(right, mirrored=True, timestamp_nanos=1).blend_horizontal, -0.7)
        self.assertTrue(math.isnan(r.iris_horizontal))
        iris = EyeSignals(iris_image_left=IrisPosition(0.2, 0.0), iris_image_right=IrisPosition(0.2, 0.0))
        self.assertGreater(estimate(iris, False, 1).iris_horizontal, 0.5)  # toward image-left = person's right
        closed = EyeSignals(blink_left=0.9, blink_right=0.9)
        self.assertFalse(estimate(closed, False, 1).eyes_open)
        down = EyeSignals(blink_left=0.9, blink_right=0.9, look_down_left=0.6, look_down_right=0.6)
        self.assertTrue(estimate(down, False, 1).eyes_open)

    def test_iris_geometry(self):
        p = iris_position(Point2(0.1, 0.5), Point2(0.3, 0.5), Point2(0.2, 0.45), Point2(0.2, 0.55), Point2(0.15, 0.52))
        self.assertAlmostEqual(p.across, 0.25)
        self.assertAlmostEqual(p.down, 0.1)
        self.assertIsNone(iris_position(Point2(0.1, 0.5), Point2(0.3, 0.5), Point2(0.2, 0.5), Point2(0.2, 0.5), Point2(0.2, 0.5)))

    def test_eye_assist_dead_zone_gain_and_fade(self):
        ea = EyeAssist(EyeAssistSettings(enabled=True, dead_zone=0.25, gain_degrees=30, max_degrees=20, smoothing_seconds=0.0))
        open_right = lambda h: __import__("headtrack_pc.gaze", fromlist=["GazeReading"]).GazeReading(h, 0.0, math.nan, math.nan, True, 0)
        self.assertEqual(ea.target(open_right(0.2)), (0.0, 0.0))
        self.assertAlmostEqual(ea.target(open_right(0.625))[0], 15.0)
        self.assertEqual(ea.target(open_right(1.0)), (20.0, 0.0))
        self.assertEqual(ea.target(None), (0.0, 0.0))
        self.assertEqual(EyeAssist(EyeAssistSettings(enabled=False)).target(open_right(1.0)), (0.0, 0.0))
        out = ea.apply(HeadPose(10, 0, 0), open_right(1.0), True, 0)
        self.assertEqual(out.yaw, 30.0)
        self.assertEqual(ea.apply(HeadPose(10, 0, 0), open_right(1.0), False, 1).yaw, 10.0)
        smooth = EyeAssist(EyeAssistSettings(enabled=True, smoothing_seconds=0.2, dead_zone=0.0, gain_degrees=30))
        smooth.apply(HeadPose(0, 0, 0), open_right(1.0), True, 0)
        mid = smooth.apply(HeadPose(0, 0, 0), open_right(1.0), True, 200 * MS)
        self.assertTrue(0 < mid.yaw < 30)
        for i in range(2, 40):
            last = smooth.apply(HeadPose(0, 0, 0), open_right(1.0), True, i * 200 * MS)
        self.assertAlmostEqual(last.yaw, 30.0, delta=0.1)
        for i in range(40, 80):
            last = smooth.apply(HeadPose(0, 0, 0), None, True, i * 200 * MS)
        self.assertEqual(last.yaw, 0.0)
        with self.assertRaises(ValueError):
            EyeAssistSettings(dead_zone=1.5)

    def test_screen_source_extended_view_both_axes(self):
        ea = EyeAssist(EyeAssistSettings(enabled=True, source=GazeSource.SCREEN, dead_zone=0.2, gain_degrees=30, max_degrees=30,
                                         gain_degrees_y=15, max_degrees_y=10, smoothing_seconds=0.0))
        self.assertEqual(ea.target(None, screen=(0.5, 0.5)), (0.0, 0.0))
        yaw, pitch = ea.target(None, screen=(1.0, 0.0))  # top-right corner: look right and up
        self.assertAlmostEqual(yaw, 30.0)
        self.assertAlmostEqual(pitch, 10.0)  # limited by max_degrees_y
        yaw, pitch = ea.target(None, screen=(0.0, 1.0))
        self.assertAlmostEqual(yaw, -30.0)
        self.assertAlmostEqual(pitch, -10.0)
        self.assertEqual(ea.target(None, screen=None), (0.0, 0.0))
        out = ea.apply(HeadPose(5, 5, 0), None, True, 0, screen=(0.9, 0.5))
        self.assertGreater(out.yaw, 5.0)
        self.assertEqual(out.pitch, 5.0)
        no_v = EyeAssist(replace(ea.settings, vertical=False))
        self.assertEqual(no_v.target(None, screen=(0.5, 0.0))[1], 0.0)
        closed = __import__("headtrack_pc.gaze", fromlist=["GazeReading"]).GazeReading(0, 0, 0, 0, False, 0)
        self.assertEqual(ea.target(closed, screen=(1.0, 0.0)), (0.0, 0.0))

    def test_compensation_calibrator(self):
        cal = GazeCompensationCalibrator(duration_nanos=10, min_samples=10)
        cal.begin(0)
        for i in range(-20, 21):
            cal.add(float(i), 0.1 - 0.02 * i, True)  # eyes counter-rotate: H falls as yaw rises
        r = cal.finish()
        self.assertTrue(r.ok, r.failure)
        self.assertAlmostEqual(r.scale, 0.02, places=6)
        self.assertAlmostEqual(r.bias, 0.1, places=6)
        cal.begin(0)
        for i in range(-20, 21):
            cal.add(float(i), 0.02 * i, True)
        self.assertEqual(cal.finish().failure, CompensationFailure.WRONG_DIRECTION)
        cal.begin(0)
        for i in range(5):
            cal.add(float(i), 0.0, True)
        self.assertEqual(cal.finish().failure, CompensationFailure.TOO_FEW_SAMPLES)
        cal.begin(0)
        for i in range(20):
            cal.add(float(i % 3), -0.02 * (i % 3), True)
        self.assertEqual(cal.finish().failure, CompensationFailure.TOO_LITTLE_HEAD_TURN)
        self.assertTrue(cal.elapsed(100) is False)  # not running


class AutoCentreTest(unittest.TestCase):
    def feed(self, auto, neutral, pose_fn, seconds, hz=30):
        n = neutral
        for i in range(int(seconds * hz)):
            t = int(i * 1e9 / hz)
            drift = auto.update(pose_fn(i), n, t)
            if drift is not None:
                n = drift
        return n

    def test_drifts_toward_resting_pose_within_limit(self):
        auto = AutoCentre(AutoCentreSettings(still_seconds=1.0, rate_per_second=1.0))
        neutral = HeadPose(0, 0, 0, 0, 0, -45)
        resting = HeadPose(5.0, -3.0, 1.0, 1.0, 0, -45)
        n = self.feed(auto, neutral, lambda i: resting, 6.0)
        self.assertAlmostEqual(n.yaw, 5.0, delta=0.2)
        self.assertAlmostEqual(n.pitch, -3.0, delta=0.2)
        self.assertAlmostEqual(n.x, 1.0, delta=0.1)
        self.assertFalse(auto.active)  # converged: nothing left to adjust

    def test_no_drift_while_moving_or_far_or_disabled(self):
        auto = AutoCentre(AutoCentreSettings(still_seconds=1.0))
        neutral = HeadPose(0, 0, 0, 0, 0, -45)
        n = self.feed(auto, neutral, lambda i: HeadPose(5.0 + 3.0 * (i % 2), 0, 0, 0, 0, -45), 4.0)
        self.assertEqual(n, neutral)
        n = self.feed(auto, neutral, lambda i: HeadPose(40.0, 0, 0, 0, 0, -45), 4.0)  # looking at the side window
        self.assertEqual(n, neutral)
        self.assertFalse(auto.active)
        off = AutoCentre(AutoCentreSettings(enabled=False, still_seconds=1.0))
        self.assertEqual(self.feed(off, neutral, lambda i: HeadPose(5.0, 0, 0, 0, 0, -45), 4.0), neutral)
        self.assertIsNone(auto.update(None, neutral, 10 * 10**9))
        self.assertIsNone(auto.update(HeadPose(1, 1, 1), None, 11 * 10**9))
        with self.assertRaises(ValueError):
            AutoCentreSettings(still_seconds=0)


class FakeKeys(KeyReader):
    def __init__(self):
        self.down = set()

    def pressed(self, vk):
        return vk in self.down


class FakeJoy(JoystickReader):
    def __init__(self):
        self.mask = 0

    def buttons(self, joystick_id):
        return self.mask if joystick_id == 0 else 0


class HotkeyTest(unittest.TestCase):
    def test_parse_key(self):
        self.assertEqual(parse_key("F12"), 0x7B)
        self.assertEqual(parse_key(" f1 "), 0x70)
        self.assertEqual(parse_key("a"), ord("A"))
        self.assertEqual(parse_key("Space"), 0x20)
        self.assertIsNone(parse_key("") )
        self.assertIsNone(parse_key("ctrl+f"))

    def test_edge_detection_and_sources(self):
        e = EdgeDetector()
        self.assertTrue(e.update("k", True))
        self.assertFalse(e.update("k", True))
        self.assertFalse(e.update("k", False))
        self.assertTrue(e.update("k", True))
        fired = []
        keys, joy = FakeKeys(), FakeJoy()
        p = HotkeyPoller(HotkeySettings(recenter_key="F12", joystick_id=0, joystick_button=3), lambda: fired.append(1), keys, joy)
        self.assertFalse(p.poll_once())
        keys.down.add(0x7B)
        self.assertTrue(p.poll_once())
        self.assertFalse(p.poll_once())  # held
        keys.down.clear()
        joy.mask = 1 << 3
        self.assertTrue(p.poll_once())
        joy.mask = 1 << 2
        self.assertFalse(p.poll_once())
        self.assertEqual(len(fired), 2)
        p.update(HotkeySettings(enabled=False))
        keys.down.add(0x7B)
        self.assertFalse(p.poll_once())
        p.start(); p.stop()
        toggles, warps = [], []
        q = HotkeyPoller(HotkeySettings(recenter_key="F12", toggle_key="F11", gaze_warp_key="F10", joystick_id=0, toggle_button=1),
                         lambda: None, keys, joy, on_toggle=lambda: toggles.append(1), on_gaze_warp=lambda: warps.append(1))
        keys.down = {0x7A}
        self.assertTrue(q.poll_once())
        keys.down = {0x79}
        self.assertTrue(q.poll_once())
        joy.mask = 1 << 1
        keys.down = set()
        self.assertTrue(q.poll_once())
        self.assertEqual((len(toggles), len(warps)), (2, 1))


class ProfilesTest(unittest.TestCase):
    def test_categories_and_presets(self):
        self.assertEqual(profiles.category_of(1006), "flight")
        self.assertEqual(profiles.category_of(4525), "driving")
        self.assertEqual(profiles.category_of(999999), "driving")
        self.assertEqual(profiles.PRESETS["flight"].mapping.yaw.max_output, 170.0)
        self.assertTrue(profiles.PRESETS["flight"].mapping.x.enabled)

    def test_with_tuning_keeps_globals(self):
        base = replace(DRIVING, output=OutputSettings(udp_enabled=True, udp_port=5555))
        p = profiles.with_tuning(base, PASSTHROUGH, name="x")
        self.assertEqual(p.output.udp_port, 5555)
        self.assertEqual(p.mapping, PASSTHROUGH.mapping)
        self.assertEqual(p.name, "x")

    def test_library_saved_or_preset(self):
        with tempfile.TemporaryDirectory() as d:
            lib = profiles.ProfileLibrary(d)
            base = DRIVING
            p = lib.for_game(1006, "DCS", base)
            self.assertEqual(p.mapping, profiles.FLIGHT.mapping)
            self.assertIn("Flight", p.name)
            self.assertFalse(lib.has_saved(1006))
            custom = replace(p, mapping=p.mapping.with_axis(Axis.YAW, replace(p.mapping.yaw, sensitivity=4.0)), name="My DCS")
            lib.save_for_game(1006, custom)
            self.assertTrue(lib.has_saved(1006))
            again = lib.for_game(1006, "DCS", base)
            self.assertEqual(again.mapping.yaw.sensitivity, 4.0)
            self.assertEqual(again.name, "My DCS")
            self.assertIsNone(again.neutral_pose)
            lib.forget(1006)
            self.assertFalse(lib.has_saved(1006))


class SelfCheckTest(unittest.TestCase):
    def row(self, report, id_):
        return next(i for i in report.items if i.id == id_)

    def test_camera_rows_and_fixes(self):
        r = selfcheck.run(selfcheck.SelfCheckInput(webcam_status="Failed", webcam_error="Camera 0 could not be opened. Is another program using it?"))
        self.assertEqual(self.row(r, "camera").result, CheckResult.FAIL)
        self.assertEqual(self.row(r, "camera").fix, "camera_privacy")
        r = selfcheck.run(selfcheck.SelfCheckInput(webcam_status="Failed", webcam_error="Face model not found: x"))
        self.assertEqual(self.row(r, "camera").fix, "fetch_model")
        r = selfcheck.run(selfcheck.SelfCheckInput(webcam_status="Starting", seconds_since_webcam_start=9))
        self.assertEqual(self.row(r, "camera").result, CheckResult.WARN)
        self.assertEqual(self.row(r, "camera").fix, "retry_camera")
        r = selfcheck.run(selfcheck.SelfCheckInput(webcam_wanted=False, phone_status="Running", phone_fresh=True))
        self.assertEqual(self.row(r, "camera").result, CheckResult.SKIP)
        self.assertEqual(self.row(r, "centre").result, CheckResult.SKIP)
        self.assertEqual(self.row(r, "phone").result, CheckResult.PASS)

    def test_game_output_and_firewall(self):
        r = selfcheck.run(selfcheck.SelfCheckInput(libs_present=False))
        self.assertEqual(self.row(r, "output").fix, "fetch_libs")
        r = selfcheck.run(selfcheck.SelfCheckInput(game_output_active=True, game_id=4525, game_name="Beamng.drive", firewall_rule_present=False))
        self.assertEqual(self.row(r, "output").result, CheckResult.PASS)
        self.assertIn("Beamng", self.row(r, "game").detail)
        self.assertEqual(self.row(r, "firewall").fix, "firewall")
        r = selfcheck.run(selfcheck.SelfCheckInput(game_output_active=True, game_id=0))
        self.assertEqual(self.row(r, "game").result, CheckResult.WARN)
        self.assertIn("anti-cheat", self.row(r, "game").detail)
        r = selfcheck.run(selfcheck.SelfCheckInput(windows=False))
        self.assertEqual(self.row(r, "output").result, CheckResult.SKIP)
        r = selfcheck.run(selfcheck.SelfCheckInput(phone_status="Failed", phone_error="Cannot listen on UDP port 4242: in use"))
        self.assertIn("opentrack", self.row(r, "phone").detail)
        good = selfcheck.run(selfcheck.SelfCheckInput(webcam_status="Running", face_detected=True, fps=30, has_neutral=True,
                                                      game_output_active=True, game_id=1, game_name="G", phone_status="Running",
                                                      discovery_on=True, firewall_rule_present=True))
        self.assertEqual(good.failed, 0)
        self.assertEqual(good.warned, 0)
        self.assertTrue(good.headline.startswith("All"))
        self.assertEqual(self.row(good, "game").detail, "G loaded the tracker DLL (ID 1)")
