import time
import unittest
from dataclasses import replace
from typing import Optional

from headtrack_pc import protocol as P
from headtrack_pc.engine import CalibrationPhase, TrackingEngine
from headtrack_pc.faceloss import TrackingState
from headtrack_pc.filters import FilterType, SmoothingSettings
from headtrack_pc.inputs.base import Frame, PoseSource, SourceStatus
from headtrack_pc.pose import Axis, HeadPose
from headtrack_pc.profile import PASSTHROUGH, PhoneSettings, SourceKind

MS = 1_000_000


class FakeClock:
    def __init__(self):
        self.now = 10 * 1_000_000_000

    def __call__(self):
        return self.now

    def advance_ms(self, ms):
        self.now += ms * MS


class FakeSource(PoseSource):
    def __init__(self):
        self.cb = None
        self.status = SourceStatus.STOPPED

    def start(self, on_frame):
        self.cb = on_frame
        self.status = SourceStatus.RUNNING

    def stop(self):
        self.status = SourceStatus.STOPPED

    def push(self, pose: Optional[HeadPose], ts=0):
        self.cb(Frame(pose, ts))


class FakeOutput:
    name = "fake"
    is_game_output = True
    game_name = "Fake game"

    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, pose, raw=None, flags=0, timestamp_nanos=0):
        self.written.append((pose, raw, flags))

    def close(self):
        self.closed = True


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        profile = replace(PASSTHROUGH, smoothing=SmoothingSettings(type=FilterType.NONE),
                          phone=PhoneSettings(stale_after_millis=500))
        self.engine = TrackingEngine(profile, clock=self.clock)
        self.out = FakeOutput()
        self.engine.add_output(self.out)
        self.engine.start()
        self.cam = FakeSource()
        self.engine.set_source(SourceKind.WEBCAM, self.cam)
        self.engine.run_sync(lambda: None)

    def tearDown(self):
        self.engine.stop()
        self.assertTrue(self.out.closed)

    def settle(self):
        self.engine.run_sync(lambda: None)

    def feed(self, pose, n=1, step_ms=40):
        # settle after every frame: the worker stamps frames with the clock at processing time
        for _ in range(n):
            self.clock.advance_ms(step_ms)
            self.cam.push(pose, self.clock.now)
            self.settle()

    def test_uncalibrated_sends_neutral_and_shows_raw(self):
        self.feed(HeadPose(10, 0, 0, 0, 0, -40))
        st = self.engine.state
        self.assertEqual(st.raw.yaw, 10.0)
        self.assertFalse(st.has_neutral)
        self.assertEqual(self.out.written[-1][0].yaw, 0.0)
        self.assertEqual(st.webcam_status, SourceStatus.RUNNING)
        self.assertEqual(st.game_name, "Fake game")
        self.assertTrue(self.engine.capabilities() & P.CAP_GAME_OUTPUT)
        self.assertTrue(self.engine.capabilities() & P.CAP_LOCAL_TRACKING)

    def test_calibration_flow_then_tracking(self):
        self.engine.calibrate()
        self.settle()
        self.assertEqual(self.engine.state.calibration, CalibrationPhase.COUNTDOWN)
        self.assertGreater(self.engine.state.calibration_seconds_left, 2.5)
        self.feed(HeadPose(5, -30, 1, 0, 0, -40), n=70)  # 2.8 s countdown ... then sampling 1 s
        self.feed(HeadPose(5, -30, 1, 0, 0, -40), n=40)
        st = self.engine.state
        self.assertEqual(st.calibration, CalibrationPhase.DONE, st.calibration_message)
        self.assertTrue(st.has_neutral)
        self.feed(HeadPose(5, -30, 1, 0, 0, -40))
        self.assertAlmostEqual(self.out.written[-1][0].yaw, 0.0, delta=1e-6)
        self.assertTrue(self.out.written[-1][2] & P.FLAG_CALIBRATED)
        self.assertTrue(self.out.written[-1][2] & P.FLAG_TRACKING_VALID)

    def test_calibration_fails_without_face(self):
        self.engine.calibrate()
        self.feed(None, n=120)
        st = self.engine.state
        self.assertEqual(st.calibration, CalibrationPhase.FAILED)
        self.assertIn("No face", st.calibration_message)

    def test_recenter_and_idle_resend(self):
        self.feed(HeadPose(20, 0, 0, 0, 0, -40))
        self.engine.recenter()
        self.settle()
        self.assertTrue(self.engine.state.has_neutral)
        self.feed(HeadPose(30, 0, 0, 0, 0, -40))
        self.assertAlmostEqual(self.engine.state.calibrated.yaw, 10.0, delta=1e-3)
        n = len(self.out.written)
        for _ in range(3):  # idle ticks (10 Hz) keep writing the face-loss pose while the clock moves on
            self.clock.advance_ms(1000)
            time.sleep(0.25)
        self.assertGreater(len(self.out.written), n)
        self.assertEqual(self.out.written[-1][0].yaw, 0.0)
        self.assertEqual(self.engine.state.tracking, TrackingState.NEUTRAL)
        self.engine.clear_centre()
        self.settle()
        self.assertFalse(self.engine.state.has_neutral)

    def test_phone_overrides_webcam_while_fresh(self):
        phone = FakeSource()
        self.engine.set_source(SourceKind.PHONE, phone)
        self.settle()
        self.clock.advance_ms(10)
        phone.push(HeadPose(42, 0, 0), self.clock.now)
        self.settle()
        self.assertEqual(self.out.written[-1][0].yaw, 42.0)
        self.assertEqual(self.engine.state.active_source, SourceKind.PHONE)
        self.assertTrue(self.engine.state.phone_fresh)
        # webcam frames are previewed but do not drive the outputs
        self.feed(HeadPose(1, 0, 0, 0, 0, -40))
        self.assertEqual(self.out.written[-1][0].yaw, 42.0)
        self.assertEqual(self.engine.state.raw.yaw, 1.0)
        # phone goes quiet: hold, ease to neutral, then the webcam takes over
        self.clock.advance_ms(600)
        time.sleep(0.25)
        self.assertFalse(self.engine.state.phone_fresh)
        self.assertEqual(self.out.written[-1][0].yaw, 42.0)  # holding
        self.clock.advance_ms(3000)
        time.sleep(0.25)
        self.assertEqual(self.out.written[-1][0].yaw, 0.0)  # eased to neutral and released
        self.engine.recenter()
        self.feed(HeadPose(1, 0, 0, 0, 0, -40))
        self.assertEqual(self.engine.state.active_source, SourceKind.WEBCAM)

    def test_output_error_is_reported_not_fatal(self):
        class Broken(FakeOutput):
            name = "broken"

            def write(self, *a, **k):
                raise RuntimeError("boom")
        self.engine.add_output(Broken())
        self.feed(HeadPose(0, 0, 0, 0, 0, -40))
        self.assertEqual(self.engine.state.output_errors.get("broken"), "boom")
        self.assertEqual(len(self.out.written), 1)

    def test_update_profile_applies(self):
        from headtrack_pc.profile import DRIVING
        self.engine.recenter()  # no face yet → FAILED message
        self.settle()
        self.assertEqual(self.engine.state.calibration, CalibrationPhase.FAILED)
        self.engine.update_profile(replace(DRIVING, smoothing=SmoothingSettings(type=FilterType.NONE)))
        self.feed(HeadPose(0, 0, 0, 0, 0, -40))
        self.engine.recenter()
        self.feed(HeadPose(30, 0, 0, 0, 0, -40))
        self.assertGreater(self.out.written[-1][0].yaw, 80.0)


class EngineFeatureTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        from headtrack_pc.autocentre import AutoCentreSettings
        from headtrack_pc.gaze import EyeAssistSettings
        profile = replace(PASSTHROUGH, smoothing=SmoothingSettings(type=FilterType.NONE),
                          auto_centre=AutoCentreSettings(still_seconds=1.0, rate_per_second=2.0),
                          eye_assist=EyeAssistSettings(enabled=True, dead_zone=0.0, gain_degrees=30, smoothing_seconds=0.0))
        self.engine = TrackingEngine(profile, clock=self.clock)
        self.out = FakeOutput()
        self.engine.add_output(self.out)
        self.engine.start()
        self.cam = FakeSource()
        self.engine.set_source(SourceKind.WEBCAM, self.cam)
        self.engine.run_sync(lambda: None)

    def tearDown(self):
        self.engine.stop()

    def settle(self):
        self.engine.run_sync(lambda: None)

    def feed(self, pose, n=1, step_ms=40, eyes=None):
        for _ in range(n):
            self.clock.advance_ms(step_ms)
            self.cam.cb(Frame(pose, self.clock.now, eyes=eyes))
            self.settle()

    def test_sweep_drives_outputs_through_the_pipeline(self):
        from headtrack_pc import sim
        self.engine.start_sweep()
        self.settle()
        self.assertTrue(self.engine.sweeping)
        self.assertTrue(self.engine.pipeline.is_calibrated)  # centre set to neutral for the sweep
        # camera frames are ignored while sweeping
        n = len(self.out.written)
        self.feed(HeadPose(50, 0, 0, 0, 0, -40))
        self.assertEqual(len(self.out.written), n)
        self.clock.advance_ms(int((sim.CENTRE_SECONDS + sim.MOVE_SECONDS / 2) * 1000))
        time.sleep(0.1)  # a few 60 Hz ticks
        st = self.engine.state
        self.assertIsNotNone(st.sweep)
        self.assertEqual(st.sweep.phase.label, "Yaw right")
        self.assertAlmostEqual(self.out.written[-1][0].yaw, (25.0 - 1.0) * (90.0 / 89.0), delta=0.2)  # mapped through dead zone
        self.assertTrue(self.out.written[-1][2] & P.FLAG_SIMULATED)
        self.clock.advance_ms(int(sim.TOTAL_SECONDS * 1000))
        time.sleep(0.1)
        self.assertFalse(self.engine.sweeping)
        self.assertEqual(self.out.written[-1][0].yaw, 0.0)
        self.engine.start_sweep(); self.settle(); self.engine.stop_sweep(); self.settle()
        self.assertFalse(self.engine.sweeping)

    def test_eye_assist_adds_yaw_when_tracked(self):
        from headtrack_pc.gaze import EyeSignals
        self.feed(HeadPose(0, 0, 0, 0, 0, -40))
        self.engine.recenter()
        self.settle()
        self.feed(HeadPose(0, 0, 0, 0, 0, -40), eyes=EyeSignals(look_in_left=1.0, look_out_right=1.0))
        self.assertAlmostEqual(self.out.written[-1][0].yaw, 30.0, delta=0.01)
        self.assertTrue(self.out.written[-1][2] & P.FLAG_EYE_ASSIST)
        self.assertAlmostEqual(self.engine.state.eye_yaw_degrees, 30.0)
        self.assertIsNotNone(self.engine.state.gaze)
        self.feed(HeadPose(0, 0, 0, 0, 0, -40), eyes=None)
        self.assertEqual(self.out.written[-1][0].yaw, 0.0)
        self.feed(None, eyes=EyeSignals(look_in_left=1.0, look_out_right=1.0))  # face lost: no eye yaw
        self.assertEqual(self.engine.state.eye_yaw_degrees, 0.0)

    def test_auto_centre_drifts_neutral(self):
        self.feed(HeadPose(0, 0, 0, 0, 0, -40))
        self.engine.recenter()
        self.settle()
        self.feed(HeadPose(4.0, 0, 0, 0, 0, -40), n=90)  # 3.6 s still, 4° off centre
        self.assertLess(abs(self.engine.state.calibrated.yaw), 0.5)
        self.assertAlmostEqual(self.engine.pipeline.neutral.yaw, 4.0, delta=0.5)
        self.assertTrue(self.engine.state.auto_centre_active or self.engine.pipeline.neutral.yaw > 3.0)
        self.feed(HeadPose(40.0, 0, 0, 0, 0, -40), n=90)  # long look aside: untouched
        self.assertAlmostEqual(self.engine.pipeline.neutral.yaw, 4.0, delta=0.5)

    def test_gaze_calibration_updates_profile_and_notifies(self):
        from headtrack_pc.gaze import EyeSignals
        got = []
        self.engine.add_profile_listener(got.append)
        self.feed(HeadPose(0, 0, 0, 0, 0, -40))
        self.engine.recenter()
        self.settle()
        self.engine.start_gaze_calibration()
        self.settle()
        self.assertGreaterEqual(self.engine.state.gaze_calibration_progress, 0.0)
        for i in range(-30, 31):
            yaw = float(i)
            h = 0.05 - 0.01 * yaw  # eyes counter-rotate
            eyes = EyeSignals(look_in_left=max(0.0, h), look_out_right=max(0.0, h), look_out_left=max(0.0, -h), look_in_right=max(0.0, -h))
            self.feed(HeadPose(yaw, 0, 0, 0, 0, -40), step_ms=100, eyes=eyes)
        self.assertLess(self.engine.state.gaze_calibration_progress, 0.0)
        self.assertTrue(self.engine.profile.eye_assist.head_compensation, self.engine.state.gaze_calibration_message)
        self.assertEqual(len(got), 1)
        self.assertIn("Compensation set", self.engine.state.gaze_calibration_message)


class AppGameSwitchTest(unittest.TestCase):
    def test_game_profile_switching_and_saving(self):
        import os
        import tempfile
        from headtrack_pc import profiles
        from headtrack_pc.app import App
        from headtrack_pc.engine import EngineState
        from headtrack_pc.hotkeys import HotkeyPoller, HotkeySettings
        from headtrack_pc.profile import OutputSettings, PhoneSettings
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "profile.json")
            prof_ = replace(PASSTHROUGH, output=OutputSettings(freetrack_enabled=False), phone=PhoneSettings(track_port=0, discovery_enabled=False),
                            hotkeys=HotkeySettings(enabled=False))
            app = App(prof_, path, log=lambda *_: None, library=profiles.ProfileLibrary(os.path.join(d, "games")),
                      hotkeys=HotkeyPoller(HotkeySettings(enabled=False), lambda: None, keys=None, joysticks=None))
            self.assertEqual(app.tuning_label().split(":")[0], "No game connected")
            app._on_engine_state(EngineState(game_id=1006, game_name="DCS"))
            self.assertEqual(app.active_game_id, 1006)
            self.assertEqual(app.profile.mapping, profiles.FLIGHT.mapping)
            self.assertIn("DCS", app.tuning_label())
            # an edit while DCS is active is saved for DCS only; the base file keeps passthrough tuning
            app.update_profile(replace(app.profile, mapping=app.profile.mapping.with_axis(Axis.YAW, replace(app.profile.mapping.yaw, sensitivity=9.0))))
            self.assertTrue(app.library.has_saved(1006))
            from headtrack_pc import profile as prof
            self.assertEqual(prof.load(path).mapping, PASSTHROUGH.mapping)
            # a global change while a game is active still lands in the base file
            app.update_profile(replace(app.profile, output=OutputSettings(freetrack_enabled=False, udp_enabled=True, udp_port=5001)))
            self.assertEqual(prof.load(path).output.udp_port, 5001)
            app._on_engine_state(EngineState(game_id=0, game_name=""))
            self.assertEqual(app.active_game_id, 0)
            self.assertEqual(app.profile.mapping, PASSTHROUGH.mapping)
            self.assertEqual(app.profile.output.udp_port, 5001)
            app._on_engine_state(EngineState(game_id=1006, game_name="DCS"))
            self.assertEqual(app.profile.mapping.yaw.sensitivity, 9.0)
            app.apply_preset("driving")
            self.assertEqual(app.profile.mapping, DRIVING_MAPPING())
            app.engine.stop()


def DRIVING_MAPPING():
    from headtrack_pc.profile import DRIVING
    return DRIVING.mapping
