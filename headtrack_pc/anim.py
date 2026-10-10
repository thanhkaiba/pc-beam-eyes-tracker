"""Small animation helpers for the Tk window: pure maths, no Tk, so they are unit-testable.

Everything is time based (seconds from `time.monotonic()`), never frame based, so a slow or
fast timer gives the same motion. The window keeps the animations light: a glide here, a
pulse there, nothing that fights the live tracking data for attention.
"""
from __future__ import annotations

import math
from typing import Optional


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def smoothstep(t: float) -> float:
    """Ease in and out: 0 → 1 with zero slope at both ends."""
    t = clamp01(t)
    return t * t * (3.0 - 2.0 * t)


def ease_out(t: float) -> float:
    """Fast start, soft landing (cubic)."""
    t = clamp01(t)
    u = 1.0 - t
    return 1.0 - u * u * u


def cycle(now: float, period: float) -> float:
    """Where in a repeating `period` seconds loop `now` falls: 0 ≤ value < 1."""
    if period <= 0.0:
        return 0.0
    return (now / period) % 1.0


def pulse(now: float, period: float) -> float:
    """A smooth 0 → 1 → 0 breath repeating every `period` seconds."""
    return 0.5 - 0.5 * math.cos(2.0 * math.pi * cycle(now, period))


class Eased:
    """A value that glides toward its target.

    Exponential smoothing with a time constant in seconds: after `seconds` the value has covered
    about 63 % of the gap, after 3× about 95 %. Because `step` takes the elapsed time, the feel
    does not depend on how often it is called.
    """

    def __init__(self, value: float = 0.0, seconds: float = 0.15, snap: float = 1e-3):
        self.value = float(value)
        self.seconds = max(1e-6, float(seconds))
        self.snap = float(snap)

    def step(self, target: float, dt: float) -> float:
        if dt > 0.0:
            k = 1.0 - math.exp(-dt / self.seconds)
            self.value += (target - self.value) * k
        if abs(target - self.value) < self.snap:
            self.value = float(target)
        return self.value

    def jump(self, value: float) -> float:
        self.value = float(value)
        return self.value


class Transition:
    """A one-shot 0 → 1 progress that runs for `seconds` once started (eased with `ease_out`)."""

    def __init__(self, seconds: float):
        self.seconds = max(1e-6, float(seconds))
        self._start: Optional[float] = None

    @property
    def started(self) -> bool:
        return self._start is not None

    def start(self, now: float) -> None:
        self._start = now

    def reset(self) -> None:
        self._start = None

    def raw(self, now: float) -> float:
        """Linear progress 0..1; 0 before start, 1 once finished."""
        if self._start is None:
            return 0.0
        return clamp01((now - self._start) / self.seconds)

    def progress(self, now: float) -> float:
        return ease_out(self.raw(now))

    def active(self, now: float) -> bool:
        return self._start is not None and (now - self._start) < self.seconds

    def finished(self, now: float) -> bool:
        return self._start is not None and (now - self._start) >= self.seconds


def road_dashes(phase: float, count: int = 6, length: float = 0.45):
    """Centre-line dashes of a road seen in perspective, scrolling toward the viewer.

    `phase` (0 ≤ phase < 1) is the scroll position within one dash period. Yields (t0, t1) pairs
    in (0, 1] along the road from the vanishing point (0) to the viewer (1); a dash drawn from
    depth t0 to t1 with its screen position proportional to t² looks like the real thing.
    """
    k = phase - 1.0
    while k < count:
        t0 = max(0.02, min(1.0, (k + 1.0) / count))
        t1 = max(0.02, min(1.0, (k + 1.0 + length) / count))
        if t1 > t0 and t0 < 1.0:
            yield t0, t1
        k += 1.0
