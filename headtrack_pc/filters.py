"""Timestamp-based smoothing filters (port of core `filter`)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .pose import HeadPose


class ScalarFilter:
    def filter(self, value: float, t_nanos: int) -> float:  # pragma: no cover - interface
        raise NotImplementedError

    def reset(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class PassThroughFilter(ScalarFilter):
    def filter(self, value: float, t_nanos: int) -> float:
        return value if math.isfinite(value) else 0.0

    def reset(self) -> None:
        pass


class ExponentialFilter(ScalarFilter):
    """EMA with a time constant so the strength is independent of frame rate: alpha = 1 - exp(-dt/tau)."""

    def __init__(self, tau_seconds: float):
        self.tau = tau_seconds
        self.last = math.nan
        self.last_t = 0

    def filter(self, value: float, t_nanos: int) -> float:
        if not math.isfinite(value):
            return 0.0 if math.isnan(self.last) else self.last
        if math.isnan(self.last) or self.tau <= 0.0:
            self.last, self.last_t = value, t_nanos
            return value
        dt = max(0.0, min(1.0, (t_nanos - self.last_t) / 1e9))
        self.last_t = t_nanos
        alpha = 1.0 - math.exp(-dt / self.tau)
        self.last += alpha * (value - self.last)
        return self.last

    def reset(self) -> None:
        self.last, self.last_t = math.nan, 0


class OneEuroFilter(ScalarFilter):
    """One Euro Filter (Casiez, Roustan, Vatavu 2012)."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.02, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x_prev = math.nan
        self.dx_prev = 0.0
        self.t_prev = 0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def filter(self, value: float, t_nanos: int) -> float:
        if not math.isfinite(value):
            return 0.0 if math.isnan(self.x_prev) else self.x_prev
        if math.isnan(self.x_prev):
            self.x_prev, self.dx_prev, self.t_prev = value, 0.0, t_nanos
            return value
        dt = (t_nanos - self.t_prev) / 1e9
        if dt <= 0.0:
            dt = 1.0 / 60.0
        dt = min(dt, 1.0)
        self.t_prev = t_nanos
        dx = (value - self.x_prev) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * value + (1 - a) * self.x_prev
        self.x_prev, self.dx_prev = x_hat, dx_hat
        return x_hat

    def reset(self) -> None:
        self.x_prev, self.dx_prev, self.t_prev = math.nan, 0.0, 0


class FilterType(Enum):
    NONE = "None"
    EXPONENTIAL = "Exponential"
    ONE_EURO = "One Euro"

    @property
    def label(self) -> str:
        return self.value


@dataclass(frozen=True)
class SmoothingSettings:
    type: FilterType = FilterType.ONE_EURO
    strength: float = 0.4
    one_euro_beta: float = 0.05

    def __post_init__(self):
        if not (math.isfinite(self.strength) and 0.0 <= self.strength <= 1.0):
            raise ValueError("strength must be within 0..1")
        if not (math.isfinite(self.one_euro_beta) and self.one_euro_beta >= 0.0):
            raise ValueError("oneEuroBeta must be >= 0")

    @property
    def exponential_tau_seconds(self) -> float:
        return 0.3 * self.strength

    @property
    def one_euro_min_cutoff(self) -> float:
        return 10.0 - (10.0 - 0.3) * self.strength

    def new_filter(self) -> ScalarFilter:
        if self.type is FilterType.NONE:
            return PassThroughFilter()
        if self.type is FilterType.EXPONENTIAL:
            return ExponentialFilter(self.exponential_tau_seconds)
        if self.strength <= 0.0:
            return PassThroughFilter()
        return OneEuroFilter(self.one_euro_min_cutoff, self.one_euro_beta, 1.0)


class PoseFilter:
    def __init__(self, settings: SmoothingSettings):
        self.filters = [settings.new_filter() for _ in range(6)]

    def filter(self, p: HeadPose) -> HeadPose:
        a = p.to_array()
        for i in range(6):
            a[i] = self.filters[i].filter(a[i], p.timestamp_nanos)
        return HeadPose.from_array(a, p.timestamp_nanos, p.confidence)

    def reset(self) -> None:
        for f in self.filters:
            f.reset()
