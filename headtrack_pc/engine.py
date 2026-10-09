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
from .calibration import CalibrationResult
from .faceloss import FaceLossHandler, TrackingState
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
    last_frame_nanos: int = 0


class _Cmd:
    def __init__(self, fn: Callable[[], None]):
        self.fn = fn


class TrackingEngine:
    def __init__(self, profile: TrackingProfile, clock=time.monotonic_ns):
        self.profile = profile
        self._clock = clock
        self.pipeline = TrackingPipeline(replace(profile, neutral_pose=None))  # the centre is set every launch
        self._phone_hold = FaceLossHandler(profile.face_loss)
        self._queue: "queue.Queue" = queue.Queue(maxsize=256)
        self._outputs: List[Output] = []
        self._sources: Dict[SourceKind, PoseSource] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state = EngineState()
        self._state_lock = threading.Lock()
        self._listeners: List[Callable[[EngineState], None]] = []
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
    def update_profile(self, p: TrackingProfile) -> None:
        def do():
            self.profile = p
            self.pipeline.update_profile(p)
            self._phone_hold.update_settings(p.face_loss)
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

    def _run(self) -> None:
        idle = 1.0 / max(1, self.profile.output.idle_resend_rate_hz)
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=idle)
            except queue.Empty:
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
        self._latency_ms = frame.latency_ms
        self._advance_calibration(now)
        snap = self.pipeline.process(frame.pose, now)
        if self._phone_fresh(now):
            self._publish(snap, SourceKind.PHONE)  # preview only; the phone drives the outputs
            return
        self._send(snap.output, snap.raw, now, SourceKind.WEBCAM, snap)

    def _tick(self) -> None:
        now = self._clock()
        self._advance_calibration(now)
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
            out = self.pipeline.tick(now)
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
        errors: Dict[str, str] = {}
        for o in self._outputs:
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
            for o in self._outputs:
                game = getattr(o, "game_name", "") or game
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
                last_frame_nanos=self._fps_window[-1] if self._fps_window else prev.last_frame_nanos,
            )
            state = self._state
        for fn in self._listeners:
            try:
                fn(state)
            except Exception:
                pass
