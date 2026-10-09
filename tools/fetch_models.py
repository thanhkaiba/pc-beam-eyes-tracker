#!/usr/bin/env python3
"""Downloads MediaPipe's face_landmarker.task (Apache 2.0) into headtrack_pc/models/.

Same model the Android app uses (float16, v1), so both apps see the same pose.
"""
from __future__ import annotations

import os
import sys
import urllib.request

URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
DEST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "headtrack_pc", "models")


def main() -> int:
    os.makedirs(DEST_DIR, exist_ok=True)
    dest = os.path.join(DEST_DIR, "face_landmarker.task")
    if os.path.isfile(dest) and os.path.getsize(dest) > 1_000_000:
        print(f"already present: {dest}")
        return 0
    print(f"downloading {URL}")
    tmp = dest + ".part"
    with urllib.request.urlopen(URL, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, dest)
    print(f"saved {dest} ({os.path.getsize(dest)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
