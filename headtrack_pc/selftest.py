"""`HeadTrackPC.exe --selftest`: proves the packaged program can do its job on this machine.

Imports OpenCV and MediaPipe, loads the bundled face model and runs one detection on a blank
image (exercises the native libraries), and checks the game-client DLLs are present. Prints one
line per check and returns 0 when all pass. The CI runs it on the built exe so a packaging
mistake (a library left out of the bundle) fails the build instead of the user's first launch.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable, List, Tuple

from .inputs.webcam import default_model_path
from .outputs.freetrack import find_libs_dir


def run_checks() -> List[Tuple[str, bool, str]]:
    results: List[Tuple[str, bool, str]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))

    add("Python", True, f"{sys.version.split()[0]} {'frozen' if getattr(sys, 'frozen', False) else 'source'} on {sys.platform}")
    try:
        import numpy as np
        add("NumPy", True, np.__version__)
    except Exception as e:  # pragma: no cover - only in a broken bundle
        add("NumPy", False, repr(e))
        return results
    try:
        import cv2
        add("OpenCV", True, cv2.__version__)
    except Exception as e:
        add("OpenCV", False, repr(e))
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        add("MediaPipe", True, mp.__version__)
    except Exception as e:
        add("MediaPipe", False, repr(e))
        mp = None
    model = default_model_path()
    if os.path.isfile(model):
        add("Face model", True, f"{model} ({os.path.getsize(model)} bytes)")
    else:
        add("Face model", False, f"missing: {model}")
        mp = None
    if mp is not None:
        try:
            t0 = time.monotonic()
            options = vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=model),
                running_mode=vision.RunningMode.IMAGE, num_faces=1,
                output_facial_transformation_matrixes=True, output_face_blendshapes=True)
            landmarker = vision.FaceLandmarker.create_from_options(options)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.zeros((256, 256, 3), dtype=np.uint8))
            result = landmarker.detect(image)
            landmarker.close()
            add("Face detection", True, f"model loaded and ran in {(time.monotonic() - t0) * 1000:.0f} ms (blank image: {len(result.face_landmarks)} faces)")
        except Exception as e:
            add("Face detection", False, repr(e))
    libs = find_libs_dir()
    if sys.platform == "win32" or libs:
        if libs:
            missing = [f for f in ("NPClient.dll", "NPClient64.dll", "freetrackclient.dll", "freetrackclient64.dll") if not os.path.isfile(os.path.join(libs, f))]
            add("Game client DLLs", not missing, libs if not missing else f"{libs} missing {', '.join(missing)}")
        else:
            add("Game client DLLs", False, "not found (NPClient.dll, freetrackclient.dll)")
    try:
        import tkinter
        add("Window toolkit", True, f"Tk {tkinter.TkVersion}")
    except Exception as e:
        add("Window toolkit", False, repr(e))
    return results


def run(out: Callable[[str], None] = print) -> int:
    results = run_checks()
    for name, ok, detail in results:
        out(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}")
    failed = [n for n, ok, _ in results if not ok]
    out("Self-test passed" if not failed else f"Self-test FAILED: {', '.join(failed)}")
    return 0 if not failed else 1
