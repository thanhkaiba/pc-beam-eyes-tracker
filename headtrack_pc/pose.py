"""Canonical head pose and the matrix <-> Euler conversions (port of core `pose`).

Convention (person-relative, degrees and centimetres):
  yaw   > 0 : head turned to the person's RIGHT
  pitch > 0 : head tilted UP
  roll  > 0 : right ear toward the right shoulder (tilt RIGHT)
  x > 0 right, y > 0 up, z > 0 forward (toward the camera)

This is a HEAD pose, never a gaze direction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable, Optional, Sequence

NAN = float("nan")


def wrap_degrees(deg: float) -> float:
    """Wraps an angle in degrees into the range (-180, 180]."""
    if not math.isfinite(deg):
        return deg
    d = math.fmod(deg + 180.0, 360.0)
    if d < 0:
        d += 360.0
    d -= 180.0
    return 180.0 if d == -180.0 else d


@dataclass(frozen=True, eq=False)
class HeadPose:
    yaw: float
    pitch: float
    roll: float
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    timestamp_nanos: int = 0
    confidence: float = NAN

    def _key(self) -> tuple:
        # NaN confidence compares equal to NaN (like Kotlin's Double.equals), so poses are usable in asserts/sets.
        conf = None if math.isnan(self.confidence) else self.confidence
        return (float(self.yaw), float(self.pitch), float(self.roll), float(self.x), float(self.y), float(self.z),
                int(self.timestamp_nanos), conf)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, HeadPose) and self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    @property
    def is_finite(self) -> bool:
        return all(math.isfinite(v) for v in (self.yaw, self.pitch, self.roll, self.x, self.y, self.z))

    def __sub__(self, other: "HeadPose") -> "HeadPose":
        return HeadPose(
            wrap_degrees(self.yaw - other.yaw), wrap_degrees(self.pitch - other.pitch), wrap_degrees(self.roll - other.roll),
            self.x - other.x, self.y - other.y, self.z - other.z, self.timestamp_nanos, self.confidence,
        )

    def __add__(self, other: "HeadPose") -> "HeadPose":
        return HeadPose(
            wrap_degrees(self.yaw + other.yaw), wrap_degrees(self.pitch + other.pitch), wrap_degrees(self.roll + other.roll),
            self.x + other.x, self.y + other.y, self.z + other.z, self.timestamp_nanos, self.confidence,
        )

    def scaled(self, factor: float) -> "HeadPose":
        return HeadPose(self.yaw * factor, self.pitch * factor, self.roll * factor,
                        self.x * factor, self.y * factor, self.z * factor, self.timestamp_nanos, self.confidence)

    def to_array(self) -> list:
        return [self.yaw, self.pitch, self.roll, self.x, self.y, self.z]

    def with_time(self, timestamp_nanos: int) -> "HeadPose":
        return replace(self, timestamp_nanos=timestamp_nanos)

    @staticmethod
    def from_array(a: Sequence[float], timestamp_nanos: int = 0, confidence: float = NAN) -> "HeadPose":
        if len(a) != 6:
            raise ValueError(f"expected 6 components, got {len(a)}")
        return HeadPose(a[0], a[1], a[2], a[3], a[4], a[5], timestamp_nanos, confidence)


NEUTRAL = HeadPose(0.0, 0.0, 0.0)


class Axis(Enum):
    YAW = (0, "Yaw", "°")
    PITCH = (1, "Pitch", "°")
    ROLL = (2, "Roll", "°")
    X = (3, "X", "cm")
    Y = (4, "Y", "cm")
    Z = (5, "Z", "cm")

    @property
    def index(self) -> int:
        return self.value[0]

    @property
    def label(self) -> str:
        return self.value[1]

    @property
    def unit(self) -> str:
        return self.value[2]

    @property
    def is_rotation(self) -> bool:
        return self.index < 3


_RAD2DEG = 180.0 / math.pi
_DEG2RAD = math.pi / 180.0


def _rotation_rows(a_deg: float, b_deg: float, c_deg: float) -> list:
    """Row-major 3x3 R = Ry(a) Rx(b) Rz(c) for camera-space angles in degrees."""
    a, b, c = a_deg * _DEG2RAD, b_deg * _DEG2RAD, c_deg * _DEG2RAD
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    return [
        ca * cc + sa * sb * sc, -ca * sc + sa * sb * cc, sa * cb,
        cb * sc, cb * cc, -sb,
        -sa * cc + ca * sb * sc, sa * sc + ca * sb * cc, ca * cb,
    ]


def from_rotation_rows(r00, r01, r02, r10, r11, r12, r20, r21, r22, tx, ty, tz,
                       mirrored: bool = False, timestamp_nanos: int = 0, confidence: float = NAN) -> Optional[HeadPose]:
    """Decomposes R = Ry(a) Rx(b) Rz(c) (camera space) into a person-relative pose."""
    sb = max(-1.0, min(1.0, -r12))
    b = math.asin(sb)
    cb = math.cos(b)
    if cb > 1e-6:
        a = math.atan2(r02, r22)
        c = math.atan2(r10, r11)
    else:  # gimbal lock: attribute everything to yaw
        a = math.atan2(-r20, r00)
        c = 0.0
    sign = -1.0 if mirrored else 1.0
    pose = HeadPose(
        yaw=wrap_degrees(-a * _RAD2DEG * sign),
        pitch=wrap_degrees(-b * _RAD2DEG),
        roll=wrap_degrees(c * _RAD2DEG * sign),
        x=-tx * sign, y=ty, z=tz,
        timestamp_nanos=timestamp_nanos, confidence=confidence,
    )
    return pose if pose.is_finite else None


def from_transformation_matrix(m, mirrored: bool = False, timestamp_nanos: int = 0,
                               confidence: float = NAN) -> Optional[HeadPose]:
    """Pose from a 4x4 facial transformation matrix.

    Accepts either a 4x4 matrix indexable as m[row][col] (MediaPipe Python returns a numpy
    array; translation in column 3) or a flat column-major sequence of 16 values as MediaPipe
    Android returns (m[col*4+row]). Returns None for wrong shapes or non-finite values.
    """
    try:
        if hasattr(m, "shape"):
            if tuple(m.shape) == (4, 4):
                rows = [[float(m[r][c]) for c in range(4)] for r in range(4)]
            elif tuple(m.shape) == (16,):
                flat = [float(v) for v in m]
                rows = [[flat[c * 4 + r] for c in range(4)] for r in range(4)]
            else:
                return None
        else:
            seq = list(m)
            if len(seq) == 4 and all(hasattr(r, "__len__") and len(r) == 4 for r in seq):
                rows = [[float(v) for v in r] for r in seq]
            elif len(seq) == 16:
                flat = [float(v) for v in seq]
                rows = [[flat[c * 4 + r] for c in range(4)] for r in range(4)]
            else:
                return None
    except (TypeError, ValueError):
        return None
    for r in rows:
        for v in r:
            if not math.isfinite(v):
                return None
    return from_rotation_rows(
        rows[0][0], rows[0][1], rows[0][2],
        rows[1][0], rows[1][1], rows[1][2],
        rows[2][0], rows[2][1], rows[2][2],
        rows[0][3], rows[1][3], rows[2][3],
        mirrored=mirrored, timestamp_nanos=timestamp_nanos, confidence=confidence,
    )


def matrix_from_camera_angles(a_deg, b_deg, c_deg, tx, ty, tz) -> list:
    """Flat column-major 4x4 (Android layout) from camera-space angles; for tests and simulation."""
    r = _rotation_rows(a_deg, b_deg, c_deg)
    m = [0.0] * 16
    for row in range(3):
        for col in range(3):
            m[col * 4 + row] = r[row * 3 + col]
    m[12], m[13], m[14], m[15] = tx, ty, tz, 1.0
    return m


def matrix_from_head_pose(p: HeadPose, mirrored: bool = False) -> list:
    sign = -1.0 if mirrored else 1.0
    return matrix_from_camera_angles(-p.yaw * sign, -p.pitch, p.roll * sign, -p.x * sign, p.y, p.z)


def _person_rows(p: HeadPose) -> list:
    return _rotation_rows(-p.yaw, -p.pitch, p.roll)


def _multiply(a: list, b: list, transpose_a: bool) -> list:
    r = [0.0] * 9
    for row in range(3):
        for col in range(3):
            s = 0.0
            for k in range(3):
                s += (a[k * 3 + row] if transpose_a else a[row * 3 + k]) * b[k * 3 + col]
            r[row * 3 + col] = s
    return r


def _pose_from_rows(r: list) -> Optional[HeadPose]:
    return from_rotation_rows(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], 0.0, 0.0, 0.0)


def relative_to(pose: HeadPose, neutral: HeadPose) -> HeadPose:
    """Rotation of `pose` relative to `neutral` as R_neutralᵀ · R_pose; translation is a plain difference.

    Composing rotations removes the fixed camera-to-head offset (camera below or beside the
    face), so a pure head turn stays pure yaw instead of leaking into roll.
    """
    rot = _pose_from_rows(_multiply(_person_rows(neutral), _person_rows(pose), transpose_a=True))
    if rot is None:
        return pose - neutral
    return replace(rot, x=pose.x - neutral.x, y=pose.y - neutral.y, z=pose.z - neutral.z,
                   timestamp_nanos=pose.timestamp_nanos, confidence=pose.confidence)


def apply_relative(neutral: HeadPose, delta: HeadPose) -> HeadPose:
    """Inverse of relative_to: the raw pose that is `delta` away from `neutral`."""
    rot = _pose_from_rows(_multiply(_person_rows(neutral), _person_rows(delta), transpose_a=False))
    if rot is None:
        return neutral
    return replace(rot, x=neutral.x + delta.x, y=neutral.y + delta.y, z=neutral.z + delta.z,
                   timestamp_nanos=delta.timestamp_nanos, confidence=delta.confidence)


def compose_matrices(a: Sequence[float], b: Sequence[float]) -> list:
    """a · b for flat column-major 4x4 matrices (test helper)."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return out
