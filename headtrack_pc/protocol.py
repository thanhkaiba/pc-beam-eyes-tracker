"""Byte-exact packet codecs shared with the Android app (see docs/protocol.md there and
docs/discovery.md here): opentrack 48-byte pose, HeadTrack 72-byte extended pose, 20-byte ping
and the 16-byte / variable discovery request and reply."""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Optional, Union

from .pose import HeadPose

# --- opentrack "UDP over network" ---------------------------------------------------------

OPENTRACK_SIZE = 48
DEFAULT_PORT = 4242
MAX_ANGLE = 180.0
MAX_TRANSLATION = 500.0
_POSE = struct.Struct("<6d")


def _clamp(v: float, lim: float) -> float:
    return max(-lim, min(lim, v))


def opentrack_values(pose: HeadPose) -> list:
    """The six values in opentrack order [TX, TY, TZ, Yaw, Pitch, Roll], clamped."""
    return [_clamp(pose.x, MAX_TRANSLATION), _clamp(pose.y, MAX_TRANSLATION), _clamp(pose.z, MAX_TRANSLATION),
            _clamp(pose.yaw, MAX_ANGLE), _clamp(pose.pitch, MAX_ANGLE), _clamp(pose.roll, MAX_ANGLE)]


def encode_opentrack(pose: HeadPose) -> bytes:
    if not pose.is_finite:
        raise ValueError("refusing to encode non-finite pose")
    return _POSE.pack(*opentrack_values(pose))


def decode_opentrack(data: bytes) -> Optional[HeadPose]:
    """First 48 bytes → pose; None for short, non-finite or out-of-range packets."""
    if len(data) < OPENTRACK_SIZE:
        return None
    x, y, z, yaw, pitch, roll = _POSE.unpack_from(data, 0)
    p = HeadPose(yaw, pitch, roll, x, y, z)
    if not p.is_finite:
        return None
    if any(abs(v) > MAX_ANGLE for v in (yaw, pitch, roll)):
        return None
    if any(abs(v) > MAX_TRANSLATION for v in (x, y, z)):
        return None
    return p


# --- HeadTrack extended (HTRK/1) ----------------------------------------------------------

HTRK_MAGIC = b"HTRK"
HTRK_VERSION = 1
HTRK_SIZE = 72
FLAG_TRACKING_VALID = 0x01
FLAG_CALIBRATED = 0x02
FLAG_SIMULATED = 0x04
FLAG_FACE_LOST_HOLD = 0x08
FLAG_EYE_ASSIST = 0x10
_TRAILER = struct.Struct("<4sBBHIQf")


@dataclass(frozen=True)
class ExtendedPacket:
    pose: HeadPose
    version: int
    flags: int
    sequence: int
    timestamp_nanos: int
    confidence: float

    @property
    def tracking_valid(self) -> bool:
        return bool(self.flags & FLAG_TRACKING_VALID)

    @property
    def calibrated(self) -> bool:
        return bool(self.flags & FLAG_CALIBRATED)

    @property
    def simulated(self) -> bool:
        return bool(self.flags & FLAG_SIMULATED)


@dataclass(frozen=True)
class Invalid:
    reason: str


def encode_extended(pose: HeadPose, sequence: int, timestamp_nanos: int, flags: int,
                    confidence: float = math.nan) -> bytes:
    return encode_opentrack(pose) + _TRAILER.pack(HTRK_MAGIC, HTRK_VERSION, flags & 0xFF, 0,
                                                  sequence & 0xFFFFFFFF, timestamp_nanos & 0xFFFFFFFFFFFFFFFF, confidence)


def decode_pose_packet(data: bytes) -> Union[ExtendedPacket, HeadPose, Invalid]:
    """A 48-byte packet decodes to a plain HeadPose, a 72-byte one to ExtendedPacket."""
    n = len(data)
    if n < OPENTRACK_SIZE:
        return Invalid(f"packet too short: {n} bytes")
    pose = decode_opentrack(data)
    if pose is None:
        return Invalid("pose prefix contains non-finite or out-of-range values")
    if n == OPENTRACK_SIZE:
        return pose
    if n < HTRK_SIZE:
        return Invalid(f"trailer truncated: {n} bytes")
    magic, version, flags, _res, seq, ts, conf = _TRAILER.unpack_from(data, OPENTRACK_SIZE)
    if magic != HTRK_MAGIC:
        return Invalid("bad magic 0x%08x" % int.from_bytes(magic, "little"))
    if version != HTRK_VERSION:
        return Invalid(f"unsupported version {version}")
    if not math.isnan(conf) and not (0.0 <= conf <= 1.0):
        return Invalid("confidence out of range")
    return ExtendedPacket(pose, version, flags, seq, ts, conf)


def sequence_delta(a: int, b: int) -> int:
    d = (b - a) & 0xFFFFFFFF
    return d - 0x1_0000_0000 if d > 0x7FFFFFFF else d


# --- ping (HTPG/1) ------------------------------------------------------------------------

PING_MAGIC = b"HTPG"
PING_VERSION = 1
PING_SIZE = 20
PING_REQUEST = 0
PING_REPLY = 1
_PING = struct.Struct("<4sBBHIQ")


@dataclass(frozen=True)
class Ping:
    kind: int
    sequence: int
    timestamp_nanos: int


def encode_ping(kind: int, sequence: int, timestamp_nanos: int) -> bytes:
    return _PING.pack(PING_MAGIC, PING_VERSION, kind, 0, sequence & 0xFFFFFFFF, timestamp_nanos & 0xFFFFFFFFFFFFFFFF)


def decode_ping(data: bytes) -> Optional[Ping]:
    if len(data) != PING_SIZE:
        return None
    magic, version, kind, _res, seq, ts = _PING.unpack(data)
    if magic != PING_MAGIC or version != PING_VERSION or kind not in (PING_REQUEST, PING_REPLY):
        return None
    return Ping(kind, seq, ts)


def is_ping(data: bytes) -> bool:
    return len(data) == PING_SIZE and data[:4] == PING_MAGIC


# --- discovery (HTDQ / HTDR, version 1) ---------------------------------------------------

DISCOVERY_PORT = 4244
DISCOVERY_REQUEST_MAGIC = b"HTDQ"
DISCOVERY_REPLY_MAGIC = b"HTDR"
DISCOVERY_VERSION = 1
DISCOVERY_KIND_REQUEST = 0
DISCOVERY_KIND_REPLY = 1
DISCOVERY_REQUEST_SIZE = 16
DISCOVERY_REPLY_HEADER = 16
DISCOVERY_MAX_NAME = 64
CAP_ACCEPTS_POSE = 1 << 0
CAP_ACCEPTS_EXTENDED = 1 << 1
CAP_ANSWERS_PINGS = 1 << 2
CAP_GAME_OUTPUT = 1 << 3
CAP_LOCAL_TRACKING = 1 << 4
_DREQ = struct.Struct("<4sBBHII")
_DREP = struct.Struct("<4sBBHIHBB")


@dataclass(frozen=True)
class DiscoveryReply:
    track_port: int
    nonce: int
    capabilities: int
    name: Optional[str]


def encode_discovery_request(nonce: int) -> bytes:
    return _DREQ.pack(DISCOVERY_REQUEST_MAGIC, DISCOVERY_VERSION, DISCOVERY_KIND_REQUEST, 0, nonce & 0xFFFFFFFF, 0)


def decode_discovery_request(data: bytes) -> Optional[int]:
    """Returns the nonce, or None if this is not a valid request."""
    if len(data) != DISCOVERY_REQUEST_SIZE:
        return None
    magic, version, kind, _r1, nonce, _r2 = _DREQ.unpack(data)
    if magic != DISCOVERY_REQUEST_MAGIC or version != DISCOVERY_VERSION or kind != DISCOVERY_KIND_REQUEST:
        return None
    return nonce


def is_discovery_request(data: bytes) -> bool:
    return len(data) == DISCOVERY_REQUEST_SIZE and data[:4] == DISCOVERY_REQUEST_MAGIC


def truncate_utf8(name: str, limit: int = DISCOVERY_MAX_NAME) -> bytes:
    raw = name.encode("utf-8")
    if len(raw) <= limit:
        return raw
    cut = raw[:limit]
    # If the cut lands inside a multi-byte sequence, drop that partial sequence.
    i = len(cut) - 1
    while i >= 0 and (cut[i] & 0xC0) == 0x80:
        i -= 1
    if i >= 0 and cut[i] >= 0xC0:
        lead = cut[i]
        need = 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
        if len(cut) - i < need:
            cut = cut[:i]
    return cut


def encode_discovery_reply(track_port: int, nonce: int, capabilities: int, name: str) -> bytes:
    if not (1 <= track_port <= 65535):
        raise ValueError("track_port out of range")
    raw = truncate_utf8(name)
    return _DREP.pack(DISCOVERY_REPLY_MAGIC, DISCOVERY_VERSION, DISCOVERY_KIND_REPLY, track_port,
                      nonce & 0xFFFFFFFF, capabilities & 0xFFFF, len(raw), 0) + raw


def decode_discovery_reply(data: bytes) -> Optional[DiscoveryReply]:
    if len(data) < DISCOVERY_REPLY_HEADER:
        return None
    magic, version, kind, port, nonce, caps, name_len, _res = _DREP.unpack_from(data, 0)
    if magic != DISCOVERY_REPLY_MAGIC or version != DISCOVERY_VERSION or kind != DISCOVERY_KIND_REPLY:
        return None
    if name_len > DISCOVERY_MAX_NAME or len(data) != DISCOVERY_REPLY_HEADER + name_len:
        return None
    if not (1 <= port <= 65535):
        return None
    try:
        name: Optional[str] = data[DISCOVERY_REPLY_HEADER:].decode("utf-8") if name_len else ""
    except UnicodeDecodeError:
        name = None
    return DiscoveryReply(port, nonce, caps, name)
