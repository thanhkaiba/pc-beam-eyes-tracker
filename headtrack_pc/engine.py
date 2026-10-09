"""The tracking engine: one worker thread owns the pipeline and the outputs.

Sources (webcam, phone) push frames from their own threads into a queue; the worker runs the
pipeline on each frame and writes the result to every output. While no frames arrive, an idle
timer re-sends the face-loss-policy pose so the game always holds a safe, known pose.

Phone frames are final (the phone already calibrated/mapped/filtered them): they bypass the
pipeline and only pass through the face-loss handler, which eases to neutral when the phone
goes quiet. When both the webcam and the phone deliver, the phone wins while its packets are
fresh (`PhoneSettings.stale_after_millis`), then the webcam takes over again.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Dict, List, Optional

from . import protocol
from . import sim
from .autocentre import AutoCentre
from .calibration import CalibrationResult
from .faceloss import FaceLossHandler, TrackingState
from .gaze import CompensationResult, EyeAssist, GazeCompensationCalibrator, GazeReading, GazeSource, estimate as estimate_gaze
from .gaze_screen import CalibrationStatus, GazePoint, GazeSmoother, ScreenCalibration, ScreenGazeModel, features as gaze_features
from .inputs.base import Frame, PoseSource, SourceStatus
from .pipeline import PipelineSnapshot, TrackingPipeline
from .pose import NEUTRAL, HeadPose
from .profile import SourceKind, TrackingProfile


class CalibrationPhase(Enum):
    IDLE = "idle"
    COUNTDOWN = "countdown"
    SAMPLING = "sampling"
    DONE = "done"
    FAILED = "failed"


class Output:
    """Anything that takes a pose: FreetrackOutput, UdpOutput, or a test fake."""
    name: str = "output"

    def write(self, pose: HeadPose, raw: Optional[HeadPose] = None, flags: int = 0, timestamp_nanos: int = 0):  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        raise NotImplementedError


@dataclass(frozen=True)
class EngineState:
    raw: Optional[HeadPose] = None
    calibrated: Optional[HeadPose] = None
    filtered: Optional[HeadPose] = None
    output: HeadPose = NEUTRAL
    tracking: TrackingState = TrackingState.NEUTRAL
    has_neutral: bool = False
    active_source: Optional[SourceKind] = None
    webcam_status: SourceStatus = SourceStatus.STOPPED
    webcam_error: Optional[str] = None
    phone_status: SourceStatus = SourceStatus.STOPPED
    phone_error: Optional[str] = None
    phone_fresh: bool = False
    fps: float = 0.0
    latency_ms: float = 0.0
    frames: int = 0
    packets_out: int = 0
    calibration: CalibrationPhase = CalibrationPhase.IDLE
    calibration_seconds_left: float = 0.0
    calibration_message: str = ""
    output_errors: Dict[str, str] = field(default_factory=dict)
    game_name: str = ""
    game_id: int = 0
    last_frame_nanos: int = 0
    eye_yaw_degrees: float = 0.0
    gaze: Optional[GazeReading] = None
    auto_centre_active: bool = False
    sweep: Optional[sim.SweepPosition] = None
    sweep_progress: float = 0.0
    gaze_calibration_progress: float = -1.0   # -1 = not running
    gaze_calibration_message: str = ""
    gaze_point: Optional[GazePoint] = None     # calibrated screen gaze (smoothed), None when unknown
    screen_gaze_quality: str = "not calibrated"
    screen_calibration: Optional[CalibrationStatus] = None  # while the 9-point calibration runs
    paused: bool = False
    eye_pitch_degrees: float = 0.0


class _Cmd:
    def __init__(self, fn: Callable[[], None]):
        self.fn = fn


def replace_status(st: CalibrationStatus) -> CalibrationStatus:
    """Copy for the immutable state (the calibration mutates its own instance)."""
    return CalibrationStatus(st.index, st.total, st.point, st.phase, st.progress, st.collected, st.message)


class TrackingEngine:
    def __init__(self, profile: TrackingProfile, clock=time.monotonic_ns):
        self.profile = profile
        self._clock = clock
        self.pipeline = TrackingPipeline(replace(profile, neutral_pose=None))  # the centre is set every launch
        self._phone_hold = FaceLossHandler(profile.face_loss)
        self._eye = EyeAssist(profile.eye_assist)
        self._auto = AutoCentre(profile.auto_centre)
        self._gaze_cal = GazeCompensationCalibrator()
        self._gaze_cal_message = ""
        self._last_gaze: Optional[GazeReading] = None
        self._sweep_start: Optional[int] = None
        self._sweep_pos: Optional[sim.SweepPosition] = None
        self._sweep_progress = 0.0
        self._screen_model: Optional[ScreenGazeModel] = self._model_from_profile(profile)
        self._screen_cal: Optional[ScreenCalibration] = None
        self._screen_status: Optional[CalibrationStatus] = None
        self._gaze_smoother = GazeSmoother()
        self._gaze_point: Optional[GazePoint] = None
        self._paused = False
        self._queue: "queue.Queue" = queue.Queue(maxsize=256)
        self._outputs: List[Output] = []
        self._sources: Dict[SourceKind, PoseSource] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state = EngineState()
        self._state_lock = threading.Lock()
        self._listeners: List[Callable[[EngineState], None]] = []
        self._profile_listeners: List[Callable[[TrackingProfile], None]] = []
        self._calib_phase = CalibrationPhase.IDLE
        self._calib_started = 0
        self._calib_message = ""
        self._last_phone_nanos = 0
        self._fps_window: List[int] = []
        self._frames = 0
        self._packets_out = 0
        self._latency_ms = 0.0
        self._last_sent_nanos = 0

    # --- wiring -----------------------------------------------------------------------------
    def add_output(self, output: Output) -> None:
        self._queue.put(_Cmd(lambda: self._outputs.append(output)))

    def remove_outputs(self) -> None:
        def do():
            for o in self._outputs:
                try:
                    o.close()
                except Exception:
                    pass
            self._outputs.clear()
        self._queue.put(_Cmd(do))

    def set_source(self, kind: SourceKind, source: PoseSource) -> None:
        old = self._sources.pop(kind, None)
        if old is not None:
            old.stop()
        self._sources[kind] = source
        source.start(lambda frame, k=kind: self._on_frame(k, frame))

    def stop_source(self, kind: SourceKind) -> None:
        src = self._sources.pop(kind, None)
        if src is not None:
            src.stop()

    def source(self, kind: SourceKind) -> Optional[PoseSource]:
        return self._sources.get(kind)

    def add_listener(self, fn: Callable[[EngineState], None]) -> None:
        self._listeners.append(fn)

    @property
    def state(self) -> EngineState:
        with self._state_lock:
            return self._state

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for kind in list(self._sources):
            self.stop_source(kind)
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=3.0)
        self._thread = None
        for o in self._outputs:
            try:
                o.close()
            except Exception:
                pass
        self._outputs.clear()

    # --- commands (thread-safe, executed on the worker) ---------------------------------------
    @staticmethod
    def _model_from_profile(p: TrackingProfile) -> Optional[ScreenGazeModel]:
        sg = p.screen_gaze
        if not sg.weights:
            return None
        m = ScreenGazeModel(tuple(sg.weights), sg.rmse_x, sg.rmse_y, sg.points)
        return m if m.valid else None

    def update_profile(self, p: TrackingProfile) -> None:
        def do():
            self.profile = p
            self.pipeline.update_profile(p)
            self._phone_hold.update_settings(p.face_loss)
            self._eye.update(p.eye_assist)
            self._auto.update_settings(p.auto_centre)
            self._screen_model = self._model_from_profile(p)
            for o in self._outputs:
                if hasattr(o, "update_settings") and getattr(o, "name", "") == "mouse":
                    o.update_settings(p.mouse)
        self._queue.put(_Cmd(do))

    # --- pause (toggle hotkey): outputs hold neutral, nothing else changes ---------------------
    def toggle_pause(self) -> None:
        def do():
            self._paused = not self._paused
            self._publish(None)
        self._queue.put(_Cmd(do))

    def set_paused(self, paused: bool) -> None:
        def do():
            self._paused = paused
            self._publish(None)
        self._queue.put(_Cmd(do))

    # --- screen gaze calibration (9 points; the GUI shows the dots) ----------------------------
    def start_screen_calibration(self, points=None) -> None:
        def do():
            self._screen_cal = ScreenCalibration(points) if points else ScreenCalibration()
            self._screen_cal.begin(self._clock())
            self._screen_status = self._screen_cal.status
            self._publish(None)
        self._queue.put(_Cmd(do))

    def cancel_screen_calibration(self) -> None:
        def do():
            if self._screen_cal is not None:
                self._screen_cal.cancel()
                self._screen_status = self._screen_cal.status
                self._screen_cal = None
            self._publish(None)
        self._queue.put(_Cmd(do))

    def clear_screen_calibration(self) -> None:
        def do():
            self._screen_model = None
            self._gaze_point = None
            self._gaze_smoother.reset()
            from .profile import ScreenGazeSettings
            self.profile = replace(self.profile, screen_gaze=ScreenGazeSettings())
            for fn in self._profile_listeners:
                try:
                    fn(self.profile)
                except Exception:
                    pass
            self._publish(None)
        self._queue.put(_Cmd(do))

    def _advance_screen_gaze(self, frame: Frame, raw: Optional[HeadPose], now: int) -> Optional[GazePoint]:
        """Runs the calibration (when active) or the model; returns the smoothed gaze point."""
        f = None
        if frame.eyes is not None and raw is not None and raw.is_finite:
            f = gaze_features(frame.eyes, raw, self.profile.camera.mirrored)
        cal = self._screen_cal
        if cal is not None:
            st = cal.update(f, now)
            self._screen_status = replace_status(st)
            if not cal.running:
                self._screen_cal = None
                if cal.result is not None:
                    self._screen_model = cal.result
                    from .profile import ScreenGazeSettings
                    self.profile = replace(self.profile, screen_gaze=ScreenGazeSettings(
                        weights=tuple(cal.result.weights), rmse_x=cal.result.rmse_x, rmse_y=cal.result.rmse_y, points=cal.result.points))
                    self._gaze_smoother.reset()
                    for fn in self._profile_listeners:
                        try:
                            fn(self.profile)
                        except Exception:
                            pass
            return None
        if self._screen_model is None or f is None:
            return self._gaze_smoother.update(None) if self._screen_model is not None else None
        return self._gaze_smoother.update(self._screen_model.predict(f, now))

    def api_state(self) -> Dict:
        """JSON-ready snapshot for the local HTTP API and the OBS overlay."""
        st = self.state

        def pose(p: Optional[HeadPose]):
            return None if p is None else {"yaw": p.yaw, "pitch": p.pitch, "roll": p.roll, "x": p.x, "y": p.y, "z": p.z}
        g = st.gaze_point
        return {
            "version": 1,
            "tracking": st.tracking.value, "paused": st.paused, "source": st.active_source.value if st.active_source else None,
            "face": st.raw is not None, "fps": st.fps, "centre_set": st.has_neutral,
            "raw": pose(st.raw), "centred": pose(st.calibrated), "sent": pose(st.output),
            "eye_yaw": st.eye_yaw_degrees, "eye_pitch": st.eye_pitch_degrees,
            "gaze": None if g is None else {"x": g.x, "y": g.y, "on_screen": g.on_screen, "quality": st.screen_gaze_quality},
            "game": {"id": st.game_id, "name": st.game_name}, "phone_connected": st.phone_fresh,
        }

    # --- direction check: scripted sweep replaces the camera for ~25 s ------------------------
    def start_sweep(self) -> None:
        def do():
            if not self.pipeline.is_calibrated:
                self.pipeline.apply_calibration(NEUTRAL)
            self._sweep_start = self._clock()
            self._sweep_pos = None
            self._sweep_progress = 0.0
        self._queue.put(_Cmd(do))

    def stop_sweep(self) -> None:
        def do():
            self._sweep_start = None
            self._sweep_pos = None
            self._sweep_progress = 0.0
        self._queue.put(_Cmd(do))

    @property
    def sweeping(self) -> bool:
        return self._sweep_start is not None

    # --- eye-assist head compensation calibration ----------------------------------------------
    def start_gaze_calibration(self) -> None:
        def do():
            self._gaze_cal.begin(self._clock())
            self._gaze_cal_message = "Look at the centre of the screen and slowly turn your head left and right"
        self._queue.put(_Cmd(do))

    def cancel_gaze_calibration(self) -> None:
        def do():
            self._gaze_cal.cancel()
            self._gaze_cal_message = ""
        self._queue.put(_Cmd(do))

    def calibrate(self) -> None:
        def do():
            self._calib_phase = CalibrationPhase.COUNTDOWN
            self._calib_started = self._clock()
            self._calib_message = ""
        self._queue.put(_Cmd(do))

    def recenter(self) -> None:
        def do():
            if self.pipeline.recenter():
                self._calib_phase = CalibrationPhase.DONE
                self._calib_message = "Centre set"
            else:
                self._calib_phase = CalibrationPhase.FAILED
                self._calib_message = "No face tracked yet"
        self._queue.put(_Cmd(do))

    def clear_centre(self) -> None:
        self._queue.put(_Cmd(self.pipeline.clear_calibration))

    def run_sync(self, fn: Callable[[], None]) -> None:
        """Runs fn on the worker and waits (tests)."""
        done = threading.Event()

        def wrapped():
            try:
                fn()
            finally:
                done.set()
        self._queue.put(_Cmd(wrapped))
        done.wait(timeout=5.0)

    # --- capabilities for discovery -----------------------------------------------------------
    def capabilities(self) -> int:
        caps = protocol.CAP_ACCEPTS_POSE | protocol.CAP_ACCEPTS_EXTENDED | protocol.CAP_ANSWERS_PINGS
        st = self.state
        if any(getattr(o, "is_game_output", False) for o in self._outputs):
            caps |= protocol.CAP_GAME_OUTPUT
        if st.webcam_status is SourceStatus.RUNNING:
            caps |= protocol.CAP_LOCAL_TRACKING
        return caps

    # --- worker --------------------------------------------------------------------------------
    def _on_frame(self, kind: SourceKind, frame: Frame) -> None:
        try:
            self._queue.put_nowait((kind, frame))
        except queue.Full:
            pass  # drop: the newest frame will follow

    def add_profile_listener(self, fn: Callable[[TrackingProfile], None]) -> None:
        """Called on the worker when the engine itself changes the profile (gaze compensation result)."""
        self._profile_listeners.append(fn)

    def _run(self) -> None:
        idle = 1.0 / max(1, self.profile.output.idle_resend_rate_hz)
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=(1 / 60.0) if self._sweep_start is not None else idle)
            except queue.Empty:
                if self._sweep_start is not None:
                    self._sweep_tick(self._clock())
                else:
                    self._tick()
                continue
            if isinstance(item, _Cmd):
                try:
                    item.fn()
                except Exception as e:  # keep the worker alive
                    self._calib_message = str(e)
                self._publish(None)
                continue
            kind, frame = item
            self._handle_frame(kind, frame)

    def _phone_fresh(self, now: int) -> bool:
        return self._last_phone_nanos and (now - self._last_phone_nanos) // 1_000_000 < self.profile.phone.stale_after_millis

    def _handle_frame(self, kind: SourceKind, frame: Frame) -> None:
        now = self._clock()
        self._frames += 1
        self._fps_window.append(now)
        cutoff = now - 1_000_000_000
        while self._fps_window and self._fps_window[0] < cutoff:
            self._fps_window.pop(0)
        if kind is SourceKind.PHONE:
            self._last_phone_nanos = now
            if frame.pose is not None:
                out = self._phone_hold.update(frame.pose, now)
                self._send(out, frame.pose, now, SourceKind.PHONE)
            return
        # webcam
        if self._sweep_start is not None:
            return  # the direction check drives the outputs; camera frames are ignored meanwhile
        self._latency_ms = frame.latency_ms
        self._advance_calibration(now)
        raw = frame.pose
        if raw is not None and raw.is_finite and not self.pipeline.is_calibrating:
            drifted = self._auto.update(raw, self.pipeline.neutral, now)
            if drifted is not None:
                self.pipeline.drift_neutral(drifted)
        else:
            self._auto.update(None, self.pipeline.neutral, now)
        snap = self.pipeline.process(raw, now)
        face = raw is not None and raw.is_finite
        gaze = estimate_gaze(frame.eyes, self.profile.camera.mirrored, now) if (frame.eyes is not None and face) else None
        self._last_gaze = gaze
        self._gaze_point = self._advance_screen_gaze(frame, raw, now)
        head_yaw = snap.calibrated.yaw if snap.calibrated is not None else float("nan")
        self._advance_gaze_calibration(gaze, head_yaw, now)
        eye_active = face and snap.is_calibrated and snap.state is TrackingState.TRACKING and not self._gaze_cal.running and self._screen_cal is None
        screen = (self._gaze_point.x, self._gaze_point.y) if self._gaze_point is not None else None
        out = self._eye.apply(snap.output, gaze, eye_active, now, head_yaw, screen)
        for o in self._outputs:
            if hasattr(o, "gaze") and self._gaze_point is not None and not self._paused:
                try:
                    o.gaze(self._gaze_point.x, self._gaze_point.y)
                except Exception:
                    pass
        if self._phone_fresh(now):
            self._publish(snap, SourceKind.PHONE)  # preview only; the phone drives the outputs
            return
        self._send(out, snap.raw, now, SourceKind.WEBCAM, snap)

    def _advance_gaze_calibration(self, gaze: Optional[GazeReading], head_yaw: float, now: int) -> None:
        cal = self._gaze_cal
        if not cal.running:
            return
        if gaze is not None:
            src = self.profile.eye_assist.source
            cal.add(head_yaw, gaze.blend_horizontal if src is GazeSource.MODEL else gaze.iris_horizontal, gaze.eyes_open)
        if cal.elapsed(now):
            r: CompensationResult = cal.finish()
            if r.ok:
                ea = replace(self.profile.eye_assist, head_compensation=True, compensation_scale=r.scale,
                             compensation_bias=r.bias, compensation_source=self.profile.eye_assist.source)
                self.profile = replace(self.profile, eye_assist=ea)
                self._eye.update(ea)
                self._gaze_cal_message = f"Compensation set (scale {r.scale:.3f}/°, fit r²={r.r2:.2f}, {r.samples} frames)"
                for fn in self._profile_listeners:
                    try:
                        fn(self.profile)
                    except Exception:
                        pass
            else:
                self._gaze_cal_message = r.failure.value if r.failure else "Calibration failed"

    def _sweep_tick(self, now: int) -> bool:
        """Drives the direction-check poses through the real pipeline; True while the sweep runs."""
        start = self._sweep_start
        if start is None:
            return False
        seconds = (now - start) / 1e9
        centre = self.pipeline.neutral or NEUTRAL
        raw = sim.pose_at(seconds, centre, now)
        if raw is None:
            self._sweep_start = None
            self._sweep_pos = None
            self._sweep_progress = 1.0
            snap = self.pipeline.process(centre.with_time(now), now)
            self._send(snap.output, snap.raw, now, SourceKind.WEBCAM, snap)
            return False
        self._sweep_pos = sim.position_at(seconds)
        self._sweep_progress = sim.progress(seconds)
        snap = self.pipeline.process(raw, now)
        self._send(snap.output, snap.raw, now, SourceKind.WEBCAM, snap)
        return True

    def _tick(self) -> None:
        now = self._clock()
        self._advance_calibration(now)
        if self._gaze_cal.running:
            self._advance_gaze_calibration(None, float("nan"), now)
        if self._screen_cal is not None:
            self._advance_screen_gaze(Frame(None, now), None, now)
        if self._phone_fresh(now):
            return
        if self._last_phone_nanos:
            # the phone went quiet: hold, ease to neutral, send that last neutral once, then release
            prev = self._phone_hold.state
            out = self._phone_hold.update(None, now)
            if prev is not TrackingState.NEUTRAL:
                self._send(out, None, now, SourceKind.PHONE)
                return
        if self._sources.get(SourceKind.WEBCAM) is not None and self.pipeline.is_calibrated:
            out = self._eye.apply(self.pipeline.tick(now), None, False, now)
            self._send(out, None, now, SourceKind.WEBCAM)
        else:
            self._publish(None)

    def _advance_calibration(self, now: int) -> None:
        cal = self.profile.calibration
        if self._calib_phase is CalibrationPhase.COUNTDOWN:
            if (now - self._calib_started) // 1_000_000 >= cal.countdown_millis:
                self.pipeline.begin_calibration(now)
                self._calib_phase = CalibrationPhase.SAMPLING
        elif self._calib_phase is CalibrationPhase.SAMPLING:
            if self.pipeline.calibration_window_elapsed(now):
                r: CalibrationResult = self.pipeline.finish_calibration()
                if r.ok:
                    self._calib_phase = CalibrationPhase.DONE
                    self._calib_message = "Centre set"
                else:
                    self._calib_phase = CalibrationPhase.FAILED
                    self._calib_message = r.failure.message if r.failure else "Calibration failed"

    def _send(self, pose: HeadPose, raw: Optional[HeadPose], now: int, source: SourceKind,
              snap: Optional[PipelineSnapshot] = None) -> None:
        flags = protocol.FLAG_TRACKING_VALID if (raw is not None) else 0
        if self.pipeline.is_calibrated or source is SourceKind.PHONE:
            flags |= protocol.FLAG_CALIBRATED
        if self._sweep_start is not None:
            flags |= protocol.FLAG_SIMULATED
        if self._eye.yaw_degrees != 0.0 or self._eye.pitch_degrees != 0.0:
            flags |= protocol.FLAG_EYE_ASSIST
        if self._paused:
            pose = NEUTRAL.with_time(now)
            flags &= ~protocol.FLAG_TRACKING_VALID
        errors: Dict[str, str] = {}
        for o in self._outputs:
            if hasattr(o, "active"):
                o.active = not self._paused
            try:
                o.write(pose, raw, flags, now)
            except Exception as e:
                errors[getattr(o, "name", type(o).__name__)] = str(e)
        self._packets_out += 1
        self._last_sent_nanos = now
        self._publish(snap, source, pose, errors)

    def _publish(self, snap: Optional[PipelineSnapshot], source: Optional[SourceKind] = None,
                 output: Optional[HeadPose] = None, errors: Optional[Dict[str, str]] = None) -> None:
        now = self._clock()
        webcam = self._sources.get(SourceKind.WEBCAM)
        phone = self._sources.get(SourceKind.PHONE)
        with self._state_lock:
            prev = self._state
            seconds_left = 0.0
            if self._calib_phase is CalibrationPhase.COUNTDOWN:
                seconds_left = max(0.0, self.profile.calibration.countdown_millis / 1000.0 - (now - self._calib_started) / 1e9)
            game = ""
            game_id = 0
            for o in self._outputs:
                game = getattr(o, "game_name", "") or game
                game_id = getattr(o, "game_id", 0) or game_id
            if game_id < 0:
                game_id = 0
            self._state = EngineState(
                raw=snap.raw if snap else prev.raw,
                calibrated=snap.calibrated if snap else prev.calibrated,
                filtered=snap.filtered if snap else prev.filtered,
                output=output if output is not None else prev.output,
                tracking=snap.state if snap else (self._phone_hold.state if source is SourceKind.PHONE
                                                  else (self.pipeline.state if source is SourceKind.WEBCAM else prev.tracking)),
                has_neutral=self.pipeline.is_calibrated,
                active_source=source if source is not None else prev.active_source,
                webcam_status=webcam.status if webcam else SourceStatus.STOPPED,
                webcam_error=webcam.error if webcam else None,
                phone_status=phone.status if phone else SourceStatus.STOPPED,
                phone_error=phone.error if phone else None,
                phone_fresh=bool(self._phone_fresh(now)),
                fps=float(len(self._fps_window)),
                latency_ms=self._latency_ms,
                frames=self._frames,
                packets_out=self._packets_out,
                calibration=self._calib_phase,
                calibration_seconds_left=seconds_left,
                calibration_message=self._calib_message,
                output_errors=errors if errors is not None else prev.output_errors,
                game_name=game,
                game_id=game_id,
                last_frame_nanos=self._fps_window[-1] if self._fps_window else prev.last_frame_nanos,
                eye_yaw_degrees=self._eye.yaw_degrees,
                gaze=self._last_gaze,
                auto_centre_active=self._auto.active,
                sweep=self._sweep_pos,
                sweep_progress=self._sweep_progress if self._sweep_start is not None else 0.0,
                gaze_calibration_progress=self._gaze_cal.progress(now) if self._gaze_cal.running else -1.0,
                gaze_calibration_message=self._gaze_cal_message,
                gaze_point=self._gaze_point,
                screen_gaze_quality=self._screen_model.quality if self._screen_model is not None else "not calibrated",
                screen_calibration=self._screen_status if self._screen_cal is not None or (self._screen_status is not None and self._screen_status.phase in ("done", "failed")) else None,
                paused=self._paused,
                eye_pitch_degrees=self._eye.pitch_degrees,
            )
            state = self._state
        for fn in self._listeners:
            try:
                fn(state)
            except Exception:
                pass
