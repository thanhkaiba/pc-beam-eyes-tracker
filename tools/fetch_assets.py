#!/usr/bin/env python3
"""Downloads the window's artwork into headtrack_pc/assets/ (the app works without it, plainer).

- road_pano.jpg: "Goegap Road" by Greg Zaal, Poly Haven, CC0 (https://polyhaven.com/a/goegap_road),
  the 360° photo behind the Home page's cockpit. Cut to the band a driver's head can see
  (pitch -40..+40°) and scaled to 3200 px for the full turn: ~0.6 MB instead of the 8K original.
- lucide.ttf + LICENSE-lucide.txt: Lucide icons (ISC, https://lucide.dev), version pinned to
  match the codepoints in headtrack_pc/icons.py.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.request

DEST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "headtrack_pc", "assets")
HEADERS = {"User-Agent": "headtrack-pc (https://github.com/thanhkaiba/pc-beam-eyes-tracker)"}

PANO_ID = "goegap_road"
PANO_WIDTH = 3200      # pixels for 360° of yaw
PITCH_BAND = 40.0      # degrees kept above and below the horizon
LUCIDE = "https://cdn.jsdelivr.net/npm/lucide-static@0.460.0"


def _get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as r:
        return r.read()


def _present(name: str, min_size: int) -> bool:
    p = os.path.join(DEST_DIR, name)
    if os.path.isfile(p) and os.path.getsize(p) >= min_size:
        print(f"already present: {p}")
        return True
    return False


def _save(name: str, data: bytes) -> None:
    p = os.path.join(DEST_DIR, name)
    with open(p + ".part", "wb") as f:
        f.write(data)
    os.replace(p + ".part", p)
    print(f"saved {p} ({len(data)} bytes)")


def fetch_pano() -> None:
    if _present("road_pano.jpg", 50_000):
        return
    from PIL import Image
    url = json.loads(_get(f"https://api.polyhaven.com/files/{PANO_ID}"))["tonemapped"]["url"]
    print(f"downloading {url}")
    Image.MAX_IMAGE_PIXELS = None   # a large equirectangular photo, not a decompression bomb
    pano = Image.open(io.BytesIO(_get(url))).convert("RGB")
    height = PANO_WIDTH // 2
    pano = pano.resize((PANO_WIDTH, height), Image.LANCZOS)
    band = round(height * PITCH_BAND / 180.0)
    pano = pano.crop((0, height // 2 - band, PANO_WIDTH, height // 2 + band))
    out = io.BytesIO()
    pano.save(out, format="JPEG", quality=86, optimize=True, progressive=True)
    _save("road_pano.jpg", out.getvalue())


def fetch_icons() -> None:
    if not _present("lucide.ttf", 100_000):
        print(f"downloading {LUCIDE}/font/lucide.ttf")
        _save("lucide.ttf", _get(f"{LUCIDE}/font/lucide.ttf"))
    if not _present("LICENSE-lucide.txt", 100):
        _save("LICENSE-lucide.txt", _get(f"{LUCIDE}/LICENSE"))


def main() -> int:
    os.makedirs(DEST_DIR, exist_ok=True)
    fetch_icons()
    fetch_pano()
    return 0


if __name__ == "__main__":
    sys.exit(main())
