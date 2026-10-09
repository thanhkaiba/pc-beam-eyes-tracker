"""Everything the user can tune, persisted as JSON (port of core `config` plus PC-only settings)."""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from enum import Enum
from typing import Any, Optional

from .autocentre import AutoCentreSettings
from .calibration import CalibrationSettings
from .faceloss import FaceLossSettings
from .gaze import EyeAssistSettings, GazeSource
from .hotkeys import HotkeySettings
from .filters import FilterType, SmoothingSettings
from .mapping import TRANSLATION_DEFAULT, AxisSettings, MappingSettings, ResponseCurve
from .pose import HeadPose

SCHEMA_VERSION = 1
DEFAULT_TRACK_PORT = 4242
DEFAULT_DISCOVERY_PORT = 4244


class SourceKind(Enum):
    WEBCAM = "webcam"
    PHONE = "phone"


class FreetrackInterface(Enum):
    BOTH = "both"
    FREETRACK = "freetrack"
    NPCLIENT = "npclient"


@dataclass(frozen=True)
class CameraSettings:
    index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30
    mirrored: bool = False  # a raw webcam frame is NOT a mirror image of the person


@dataclass(frozen=True)
class OutputSettings:
    freetrack_enabled: bool = True
    freetrack_interface: FreetrackInterface = FreetrackInterface.BOTH
    udp_enabled: bool = False
    udp_host: str = "127.0.0.1"
    udp_port: int = DEFAULT_TRACK_PORT
    idle_resend_rate_hz: int = 10


@dataclass(frozen=True)
class PhoneSettings:
    track_port: int = DEFAULT_TRACK_PORT
    discovery_port: int = DEFAULT_DISCOVERY_PORT
    discovery_enabled: bool = True
    pc_name: str = ""  # empty = hostname
    stale_after_millis: int = 500


@dataclass(frozen=True)
class TrackingProfile:
    schema_version: int = SCHEMA_VERSION
    name: str = "Driving (default)"
    source: SourceKind = SourceKind.WEBCAM
    mapping: MappingSettings = MappingSettings()
    smoothing: SmoothingSettings = SmoothingSettings()
    face_loss: FaceLossSettings = FaceLossSettings()
    calibration: CalibrationSettings = CalibrationSettings()
    camera: CameraSettings = CameraSettings()
    output: OutputSettings = OutputSettings()
    phone: PhoneSettings = PhoneSettings()
    eye_assist: EyeAssistSettings = EyeAssistSettings()
    auto_centre: AutoCentreSettings = AutoCentreSettings()
    hotkeys: HotkeySettings = HotkeySettings()
    neutral_pose: Optional[HeadPose] = None


DRIVING = TrackingProfile(
    name="Driving (default)",
    mapping=MappingSettings(
        yaw=AxisSettings(sensitivity=3.0, dead_zone=1.0, max_output=90.0, curve=ResponseCurve.SOFT),
        pitch=AxisSettings(sensitivity=2.0, dead_zone=1.0, max_output=45.0, curve=ResponseCurve.SOFT),
        roll=AxisSettings(sensitivity=0.5, dead_zone=2.0, max_output=30.0, curve=ResponseCurve.LINEAR),
        x=TRANSLATION_DEFAULT, y=TRANSLATION_DEFAULT, z=TRANSLATION_DEFAULT,
    ),
    smoothing=SmoothingSettings(type=FilterType.ONE_EURO, strength=0.4, one_euro_beta=0.05),
)
PASSTHROUGH = TrackingProfile(name="Passthrough (1:1)")
DEFAULT = DRIVING


# --- JSON codec -------------------------------------------------------------------------

def _to_json(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, HeadPose):
        return {"yaw": obj.yaw, "pitch": obj.pitch, "roll": obj.roll, "x": obj.x, "y": obj.y, "z": obj.z}
    if is_dataclass(obj):
        return {f.name: _to_json(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, float) and not math.isfinite(obj):
        raise ValueError("non-finite value in profile")
    return obj


def _from_json(cls: Any, data: Any) -> Any:
    if data is None:
        return None
    if cls is HeadPose:
        p = HeadPose(float(data["yaw"]), float(data["pitch"]), float(data["roll"]),
                     float(data.get("x", 0.0)), float(data.get("y", 0.0)), float(data.get("z", 0.0)))
        if not p.is_finite:
            raise ValueError("non-finite neutral")
        return p
    if isinstance(cls, type) and issubclass(cls, Enum):
        return cls[data]
    if is_dataclass(cls):
        if not isinstance(data, dict):
            raise ValueError(f"expected object for {cls.__name__}")
        kwargs = {}
        for f in fields(cls):
            if f.name in data:
                kwargs[f.name] = _from_json(_field_type(cls, f.name), data[f.name])
        return cls(**kwargs)
    if cls is float:
        v = float(data)
        if not math.isfinite(v):
            raise ValueError("non-finite number")
        return v
    if cls is int:
        if isinstance(data, bool):
            raise ValueError("bool where int expected")
        return int(data)
    if cls is bool:
        if not isinstance(data, bool):
            raise ValueError("expected bool")
        return data
    if cls is str:
        return str(data)
    return data


_TYPES = {
    "mapping": MappingSettings, "yaw": AxisSettings, "pitch": AxisSettings, "roll": AxisSettings,
    "x": AxisSettings, "y": AxisSettings, "z": AxisSettings, "curve": ResponseCurve,
    "smoothing": SmoothingSettings, "type": FilterType, "face_loss": FaceLossSettings,
    "calibration": CalibrationSettings, "camera": CameraSettings, "output": OutputSettings,
    "freetrack_interface": FreetrackInterface, "phone": PhoneSettings, "neutral_pose": HeadPose,
    "source": SourceKind, "eye_assist": EyeAssistSettings, "gaze_source": GazeSource,
    "compensation_source": GazeSource, "auto_centre": AutoCentreSettings, "hotkeys": HotkeySettings,
}


def _field_type(cls: Any, name: str) -> Any:
    for f in fields(cls):
        if f.name == name:
            if cls is EyeAssistSettings and name == "source":
                return GazeSource
            if name in _TYPES:
                return _TYPES[name]
            t = f.type
            if isinstance(t, str):
                return {"int": int, "float": float, "bool": bool, "str": str}.get(t, str)
            return t
    raise KeyError(name)


def encode(profile: TrackingProfile) -> str:
    return json.dumps(_to_json(profile), indent=2)


def decode_or_none(text: Optional[str]) -> Optional[TrackingProfile]:
    """Never throws: unreadable or newer-schema text decodes to None."""
    if not text:
        return None
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        if int(data.get("schema_version", SCHEMA_VERSION)) > SCHEMA_VERSION:
            return None
        p = _from_json(TrackingProfile, data)
        return replace(p, schema_version=SCHEMA_VERSION)
    except Exception:
        return None


def decode_or_default(text: Optional[str]) -> TrackingProfile:
    return decode_or_none(text) or DEFAULT


# --- storage ------------------------------------------------------------------------------

def config_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "HeadTrackPC")


def profile_path() -> str:
    return os.path.join(config_dir(), "profile.json")


def load(path: Optional[str] = None) -> TrackingProfile:
    path = path or profile_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return decode_or_default(f.read())
    except OSError:
        return DEFAULT


def save(profile: TrackingProfile, path: Optional[str] = None) -> None:
    path = path or profile_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(encode(profile))
    os.replace(tmp, path)
