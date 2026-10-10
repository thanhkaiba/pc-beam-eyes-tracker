"""The Home page's driving view: a real 360° road photo seen through a drawn cockpit.

The panorama (tools/fetch_assets.py, CC0) is an equirectangular band: x is yaw over 360°, y is pitch
around the horizon. Each frame crops the part a game camera would see for the sent pose, rolls it,
and lays the cockpit on top with a little parallax. Everything is Pillow at the canvas size, a few
milliseconds per frame. Without the photo (not fetched) the window keeps its vector drawing.
"""
from __future__ import annotations

import math
import os
from typing import Optional

ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "road_pano.jpg")
FORWARD_X = 0.47       # where the road ahead is in the photo (fraction of its width)
HORIZON_Y = 0.5        # the band is centred on the horizon
FOV_DEGREES = 95.0     # horizontal field of view of the windscreen
PITCH_BAND = 40.0      # degrees above/below the horizon kept by fetch_assets.py
EYE_PITCH = -7.0       # a driver looks a little down the road, not at the horizon


def asset_path() -> Optional[str]:
    """The panorama next to the package, or inside a frozen bundle."""
    import sys
    for d in (os.path.dirname(ASSET), os.path.join(getattr(sys, "_MEIPASS", ""), "headtrack_pc", "assets")):
        p = os.path.join(d, "road_pano.jpg")
        if os.path.isfile(p):
            return p
    return None


class CockpitScene:
    def __init__(self, width: int, height: int, pano=None):
        from PIL import Image
        self.width, self.height = width, height
        if pano is None:
            path = asset_path()
            if path is None:
                raise FileNotFoundError("road panorama not fetched (tools/fetch_assets.py)")
            pano = Image.open(path)
        pano = pano.convert("RGB")
        # the windscreen's field of view a little wider than the canvas: sharp, and cheap to rotate
        full_w = round(width * 1.15 * 360.0 / FOV_DEGREES)
        self.pano = pano.resize((full_w, round(pano.height * full_w / pano.width)), Image.BILINEAR)
        self.px_per_degree = full_w / 360.0
        self.cockpit = _draw_cockpit(width, height)

    def render(self, yaw: float, pitch: float, roll: float, zoom: float = 1.0,
               parallax_x: float = 0.0, parallax_y: float = 0.0):
        """Degrees as the game camera turns (yaw right, pitch up, roll right = world turns left).
        Returns an RGB image of the canvas size."""
        from PIL import Image
        w, h = self.width, self.height
        view_w = FOV_DEGREES * self.px_per_degree / max(0.5, zoom)
        view_h = view_w * h / w
        # enough around the view that rotating it leaves no corners empty
        side = math.hypot(view_w, view_h)
        cx = FORWARD_X * self.pano.width + yaw * self.px_per_degree
        cy = HORIZON_Y * self.pano.height - (pitch + EYE_PITCH) * self.px_per_degree
        box = (round(cx - side / 2), round(cy - side / 2), round(cx + side / 2), round(cy + side / 2))
        patch = _crop_wrapped(self.pano, box)
        # one affine pass at the canvas size: output pixel → patch pixel (scale, then roll about the centre)
        k = view_w / w
        a = math.radians(-roll)   # PIL maps output→source, so the source turns the other way
        cos_a, sin_a = math.cos(a) * k, math.sin(a) * k
        c = patch.width / 2
        data = (cos_a, sin_a, c - cos_a * w / 2 - sin_a * h / 2,
                -sin_a, cos_a, c + sin_a * w / 2 - cos_a * h / 2)
        frame = patch.transform((w, h), Image.AFFINE, data, resample=Image.BILINEAR).convert("RGBA")
        # the overlay is 10 % larger: its centre sits on the canvas centre, shifted by the parallax
        sx = max(0, min(self.cockpit.width - w, round(w * 0.05 - parallax_x * w)))
        sy = max(0, min(self.cockpit.height - h, round(h * 0.05 - parallax_y * h)))
        frame.alpha_composite(self.cockpit, (0, 0), (sx, sy, sx + w, sy + h))
        return frame.convert("RGB")


def _crop_wrapped(img, box):
    """Crop that wraps around in x (the photo is a full turn) and clamps in y (sky/road edge repeats)."""
    from PIL import Image
    x0, y0, x1, y1 = box
    out = Image.new("RGB", (x1 - x0, y1 - y0))
    W, H = img.size
    sy0, sy1 = max(0, y0), min(H, y1)
    x = x0
    while x < x1:
        sx = x % W
        take = min(W - sx, x1 - x)
        if sy1 > sy0:
            out.paste(img.crop((sx, sy0, sx + take, sy1)), (x - x0, sy0 - y0))
        x += take
    # beyond the band: stretch the top and bottom rows so a big pitch still shows sky / road
    if y0 < 0 and sy1 > sy0:
        top = out.crop((0, sy0 - y0, out.width, sy0 - y0 + 1)).resize((out.width, -y0))
        out.paste(top, (0, 0))
    if y1 > H and sy1 > sy0:
        bottom = out.crop((0, sy1 - y0 - 1, out.width, sy1 - y0)).resize((out.width, y1 - H))
        out.paste(bottom, (0, sy1 - y0))
    return out


def _draw_cockpit(width: int, height: int):
    """Dashboard, pillars, roof, mirror and wheel as an RGBA overlay 10 % larger than the canvas
    (room for parallax). Drawn at 3x and scaled down for smooth edges."""
    from PIL import Image, ImageDraw, ImageFilter
    s = 3
    W, H = round(width * 1.1) * s, round(height * 1.1) * s
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    dark, mid, edge = (22, 23, 26, 255), (34, 36, 40, 255), (58, 61, 68, 255)
    # roof and A-pillars frame the windscreen
    d.polygon([(0, 0), (W, 0), (W, H * 0.07), (W * 0.86, H * 0.12), (W * 0.14, H * 0.12), (0, H * 0.07)], fill=dark)
    d.polygon([(0, 0), (W * 0.15, H * 0.11), (W * 0.035, H * 0.72), (0, H * 0.74)], fill=dark)
    d.polygon([(W, 0), (W * 0.85, H * 0.11), (W * 0.965, H * 0.72), (W, H * 0.74)], fill=dark)
    d.line([(W * 0.15, H * 0.11), (W * 0.035, H * 0.72)], fill=edge, width=2 * s)
    d.line([(W * 0.85, H * 0.11), (W * 0.965, H * 0.72)], fill=edge, width=2 * s)
    # rear-view mirror
    d.rectangle([W * 0.495, H * 0.09, W * 0.505, H * 0.14], fill=dark)
    d.rounded_rectangle([W * 0.42, H * 0.13, W * 0.58, H * 0.21], radius=6 * s, fill=(15, 16, 18, 255), outline=edge, width=s)
    d.rounded_rectangle([W * 0.43, H * 0.145, W * 0.57, H * 0.195], radius=4 * s, fill=(70, 92, 120, 255))
    # dashboard: a wide curved top edge, a lighter lip, then the body
    d.pieslice([-W * 0.25, H * 0.66, W * 1.25, H * 1.6], 180, 360, fill=mid)
    d.arc([-W * 0.25, H * 0.66, W * 1.25, H * 1.6], 180, 360, fill=edge, width=3 * s)
    d.rectangle([0, H * 0.92, W, H], fill=mid)
    # instrument cluster behind the wheel: two gauges with ticks and a glowing needle
    for gx in (0.42, 0.58):
        cx, cy, r = W * gx, H * 0.86, H * 0.075
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(12, 13, 15, 255), outline=edge, width=s)
        for i in range(9):
            a = math.radians(135 + i * 33.75)
            d.line([(cx + math.cos(a) * r * 0.72, cy + math.sin(a) * r * 0.72), (cx + math.cos(a) * r * 0.9, cy + math.sin(a) * r * 0.9)],
                   fill=(150, 200, 255, 255), width=s)
        a = math.radians(135 + 100)
        d.line([(cx, cy), (cx + math.cos(a) * r * 0.8, cy + math.sin(a) * r * 0.8)], fill=(255, 120, 60, 255), width=2 * s)
    # steering wheel: rim, three spokes, hub
    cx, cy, r = W * 0.5, H * 1.08, H * 0.36
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(14, 14, 16, 255), width=round(H * 0.045))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(48, 50, 56, 255), width=s)
    for a in (180, 0, 90):
        rad = math.radians(a)
        d.line([(cx, cy), (cx + math.cos(rad) * r, cy + math.sin(rad) * r)], fill=(20, 20, 23, 255), width=round(H * 0.035))
    d.ellipse([cx - r * 0.28, cy - r * 0.28, cx + r * 0.28, cy + r * 0.28], fill=(28, 29, 33, 255), outline=edge, width=s)
    img = img.resize((W // s, H // s), Image.LANCZOS)
    # soft shadow inside the windscreen edge so the photo sits behind glass
    shade = img.split()[3].filter(ImageFilter.GaussianBlur(6))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.putalpha(shade.point(lambda v: v * 0.45))
    return Image.alpha_composite(shadow, img)
