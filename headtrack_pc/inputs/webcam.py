"""Webcam → MediaPipe Face Landmarker (LIVE_STREAM) → HeadPose.

The frame is analysed as the camera delivers it (not mirrored), which is the `mirrored=False`
case of the pose maths: a raw webcam frame shows the person as another person would see them.
Only the on-screen preview is flipped for a mirror feel. MediaPipe's live-stream mode drops
frames while inference is busy, so there is never a queue of stale frames.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

from .. import pose as posemath
from ..profile import CameraSettings
from .base import Frame, FrameCallback, PoseSource, SourceStatus

MODEL_FILE = "face_landmarker.task"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"


def default_model_path() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [os.path.join(here, "models", MODEL_FILE)]
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "models", MODEL_FILE))
        candidates.append(os.path.join(getattr(sys, "_MEIPASS", ""), "headtrack_pc", "models", MODEL_FILE))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0]


class WebcamSource(PoseSource):
    def __init__(self, settings: CameraSettings, model_path: Optional[str] = None, clock=time.monotonic_ns):
        self.settings = settings
        self.model_path = model_path or default_model_path()
        self._clock = clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._on_frame: Optional[FrameCallback] = None
        self._landmarker = None
        self._lock = threading.Lock()
        self._preview = None  # latest BGR frame (numpy) for the UI
        self._submitted = {}
        self.frames_captured = 0
        self.frames_tracked = 0
        self.status = SourceStatus.STOPPED
        self.error = None

    # --- lifecycle -------------------------------------------------------------------
    def start(self, on_frame: FrameCallback) -> None:
        self._on_frame = on_frame
        self._stop.clear()
        self.status = SourceStatus.STARTING
        self.error = None
        self._thread = threading.Thread(target=self._run, name="webcam", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=3.0)
        self.status = SourceStatus.STOPPED

    def preview(self):
        with self._lock:
            return self._preview

    # --- worker ----------------------------------------------------------------------
    def _fail(self, message: str) -> None:
        self.error = message
        self.status = SourceStatus.FAILED

    def _run(self) -> None:
        try:
            import cv2  # noqa: WPS433
        except ImportError:
            return self._fail("OpenCV (opencv-python) is not installed")
        if not os.path.isfile(self.model_path):
            return self._fail(f"Face model not found: {self.model_path}. Run `python tools/fetch_models.py`.")
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
        except ImportError:
            return self._fail("MediaPipe is not installed (pip install mediapipe)")

        s = self.settings
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(s.index, backend)
        if not cap.isOpened():
            cap.release()
            return self._fail(f"Camera {s.index} could not be opened. Is another program using it?")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, s.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, s.height)
        cap.set(cv2.CAP_PROP_FPS, s.fps)

        def on_result(result, image, timestamp_ms):
            now = self._clock()
            ts_nanos = timestamp_ms * 1_000_000
            matrices = result.facial_transformation_matrixes
            pose = None
            if matrices:
                pose = posemath.from_transformation_matrix(matrices[0], mirrored=s.mirrored, timestamp_nanos=ts_nanos)
            landmarks = len(result.face_landmarks[0]) if result.face_landmarks else 0
            submitted = self._submitted.pop(timestamp_ms, None)
            latency = (now - submitted) / 1e6 if submitted else 0.0
            if pose is not None:
                self.frames_tracked += 1
            cb = self._on_frame
            if cb is not None:
                cb(Frame(pose, ts_nanos, latency, landmarks))

        try:
            options = vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=self.model_path),
                running_mode=vision.RunningMode.LIVE_STREAM,
                num_faces=1,
                min_face_detection_confidence=0.5,
                min_face_presence_confidence=0.5,
                min_tracking_confidence=0.5,
                output_facial_transformation_matrixes=True,
                output_face_blendshapes=False,
                result_callback=on_result,
            )
            self._landmarker = vision.FaceLandmarker.create_from_options(options)
        except Exception as e:  # model corrupt, unsupported CPU, ...
            cap.release()
            return self._fail(f"MediaPipe could not start: {e}")

        self.status = SourceStatus.RUNNING
        last_ts = -1
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.01)
                    continue
                self.frames_captured += 1
                ts_ms = self._clock() // 1_000_000
                if ts_ms <= last_ts:
                    ts_ms = last_ts + 1  # MediaPipe needs strictly increasing timestamps
                last_ts = ts_ms
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                self._submitted[ts_ms] = self._clock()
                if len(self._submitted) > 64:
                    for k in sorted(self._submitted)[:-32]:
                        self._submitted.pop(k, None)
                try:
                    self._landmarker.detect_async(image, ts_ms)
                except Exception as e:
                    self._fail(f"MediaPipe inference failed: {e}")
                    break
                with self._lock:
                    self._preview = frame
        finally:
            cap.release()
            try:
                self._landmarker.close()
            except Exception:
                pass
            self._landmarker = None
            if self.status is SourceStatus.RUNNING:
                self.status = SourceStatus.STOPPED
