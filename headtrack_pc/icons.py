"""Lucide icons (ISC, https://lucide.dev) drawn from their font with Pillow into Tk images.

Tk cannot load a font file or draw SVG, so each icon is rendered once per (name, size, colour)
into a transparent PhotoImage: it then works in any button or label, on any background.
The font comes from tools/fetch_assets.py; without it every icon is None and the widgets show
their text only.
"""
from __future__ import annotations

import os
import sys
from typing import Dict, Optional, Tuple

# codepoints of lucide-static 0.460.0 (pinned in tools/fetch_assets.py)
ICONS = {
    "house": 0xe0f8, "smartphone": 0xe167, "settings": 0xe158, "circle-help": 0xe082, "crosshair": 0xe0b0,
    "pause": 0xe132, "play": 0xe140, "scan-eye": 0xe53b, "scan-face": 0xe375, "camera": 0xe068, "webcam": 0xe205,
    "wifi": 0xe1ae, "car": 0xe1d5, "plane": 0xe1de, "equal": 0xe1bd, "gamepad-2": 0xe0e2, "shield-check": 0xe1ff,
    "shield-alert": 0xe1fe, "copy": 0xe0a2, "eye": 0xe0be, "mouse-pointer-2": 0xe1c3, "keyboard": 0xe284,
    "monitor": 0xe121, "sliders-horizontal": 0xe29a, "check": 0xe070, "triangle-alert": 0xe193, "x": 0xe1b2,
    "chevron-right": 0xe073, "chevron-down": 0xe071, "rotate-ccw": 0xe14c, "search": 0xe155, "arrow-left": 0xe04c,
    "arrow-right": 0xe04d, "radio": 0xe146, "refresh-cw": 0xe149, "circle-check": 0xe226, "circle-x": 0xe088,
    "circle-minus": 0xe083, "info": 0xe0fe, "cast": 0xe06a, "activity": 0xe038, "flip-horizontal-2": 0xe362,
    "external-link": 0xe0bd, "list": 0xe10c, "wand-sparkles": 0xe35b, "video-off": 0xe1a6,
    "monitor-smartphone": 0xe3a6, "locate-fixed": 0xe1db,
}


def font_path() -> Optional[str]:
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    for d in (here, os.path.join(getattr(sys, "_MEIPASS", ""), "headtrack_pc", "assets")):
        p = os.path.join(d, "lucide.ttf")
        if os.path.isfile(p):
            return p
    return None


class Icons:
    def __init__(self) -> None:
        self._path = font_path()
        self._fonts: Dict[int, object] = {}
        self._cache: Dict[Tuple[str, int, str], object] = {}

    @property
    def available(self) -> bool:
        return self._path is not None

    def image(self, name: str, size: int = 16, colour: str = "#f2f2f2"):
        """A Pillow RGBA image of the icon, or None without the font."""
        if self._path is None or name not in ICONS:
            return None
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None
        # draw at 4x and shrink: the strokes stay crisp and even at 14-20 px
        big = size * 4
        font = self._fonts.get(big)
        if font is None:
            font = self._fonts[big] = ImageFont.truetype(self._path, big)
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((big / 2, big / 2), chr(ICONS[name]), font=font, fill=colour, anchor="mm")
        return img.resize((size, size), Image.LANCZOS)

    def text(self, text: str, size: int = 28, colour: str = "#f2f2f2"):
        """Large text as a transparent Tk image in Segoe UI Semibold. Tk on Windows paints big
        bold label text over a box of the theme colour, visible on the darker page; an image is
        clean on any background. None when the font or Pillow is missing (callers use a label)."""
        key = ("text:" + text, size, colour)
        if key in self._cache:
            return self._cache[key]
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageTk
            windir = os.environ.get("WINDIR", r"C:\Windows")
            path = next((p for p in (os.path.join(windir, "Fonts", f) for f in ("seguisb.ttf", "segoeuib.ttf")) if os.path.isfile(p)), None)
            if path is None or not text:
                return None
            font = ImageFont.truetype(path, size * 2)
            left, top, right, bottom = font.getbbox(text)
            asc, desc = font.getmetrics()
            img = Image.new("RGBA", (right + 4, asc + desc), (0, 0, 0, 0))
            ImageDraw.Draw(img).text((0, 0), text, font=font, fill=colour)
            img = img.resize((max(1, img.width // 2), max(1, img.height // 2)), Image.LANCZOS)
            self._cache[key] = ImageTk.PhotoImage(img)
            return self._cache[key]
        except Exception:
            return None

    def get(self, name: str, size: int = 16, colour: str = "#f2f2f2"):
        """A Tk PhotoImage (kept alive here), or None without the font."""
        key = (name, size, colour)
        if key not in self._cache:
            img = self.image(name, size, colour)
            if img is None:
                return None
            from PIL import ImageTk
            self._cache[key] = ImageTk.PhotoImage(img)
        return self._cache[key]

