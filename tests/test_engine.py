import time
import unittest
from dataclasses import replace
from typing import Optional

from headtrack_pc import protocol as P
from headtrack_pc.engine import CalibrationPhase, TrackingEngine
from headtrack_pc.faceloss import TrackingState
from headtrack_pc.filters import FilterType, SmoothingSettings
from headtrack_pc.inputs.base import Frame, PoseSource, SourceStatus
from headtrack_pc.pose import HeadPose
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
