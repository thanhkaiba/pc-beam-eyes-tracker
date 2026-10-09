"""freetrack 2.0 Enhanced / TrackIR output: what opentrack's `proto-ft` does, without opentrack.

How games read head tracking on Windows (verified against opentrack 2026.1.0 sources,
`proto-ft/ftnoir_protocol_ft.cpp`, `freetrackclient/fttypes.h`, `freetrackclient.c`):

1. The game loads `freetrackclient.dll` (FreeTrack API) or `NPClient.dll` (TrackIR API). It
   finds them through two registry values under HKEY_CURRENT_USER:
   `Software\\Freetrack\\FreetrackClient\\Path` and
   `Software\\NaturalPoint\\NATURALPOINT\\NPClient Location\\Path`
   (a directory, forward slashes, trailing slash).
2. Those DLLs open a page-file-backed file mapping named `FT_SharedMem` laid out as `FTHeap`
   (below) and copy the pose out of it under the `FT_Mutext` mutex.
3. The tracker writes the pose into the mapping each frame and increments `DataID`; the
   client treats an unchanged `DataID` as "no new data". When the game reports its ID in
   `GameID`, the tracker answers with the 8-byte key from the game table in `table` and
   echoes the ID into `GameID2` (NPClient needs this for some games).
4. Some TrackIR games also check for a running `TrackIR.exe`; opentrack starts a dummy one.

Units in the mapping: radians, yaw and pitch negated (positive yaw = left, positive pitch =
up... as the FreeTrack SDK defines them), roll as is, translation in millimetres.

The platform-independent part (`FreetrackMemory`) works on any writable buffer and is unit
tested on Linux; `FreetrackOutput` adds the Windows mapping, mutex, registry and dummy process.
"""
from __future__ import annotations

import math
import mmap
import os
import struct
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, Optional

from ..pose import HeadPose
from . import games as game_table

SHM_NAME = "FT_SharedMem"
MUTEX_NAME = "FT_Mutext"

# FTData: DataID u32, CamWidth i32, CamHeight i32, 6 pose floats, 6 raw floats, 8 point floats
_FTDATA = struct.Struct("<Iii20f")
FTDATA_SIZE = _FTDATA.size  # 92
OFF_DATA_ID = 0
OFF_CAM_WIDTH = 4
OFF_CAM_HEIGHT = 8
OFF_POSE = 12           # Yaw Pitch Roll X Y Z
OFF_RAW = 36            # RawYaw RawPitch RawRoll RawX RawY RawZ
OFF_POINTS = 60         # X1 Y1 .. X4 Y4
OFF_GAME_ID = FTDATA_SIZE          # 92
OFF_TABLE = FTDATA_SIZE + 4        # 96, 8 bytes
OFF_GAME_ID2 = FTDATA_SIZE + 12    # 104
FTHEAP_SIZE = FTDATA_SIZE + 16     # 108

_D2R = math.pi / 180.0

DLL_FILES = ("NPClient.dll", "NPClient64.dll", "freetrackclient.dll", "freetrackclient64.dll")
DUMMY_EXE = "TrackIR.exe"
LIBS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "libs")


def ft_values(pose: HeadPose) -> tuple:
    """(Yaw, Pitch, Roll, X, Y, Z) exactly as opentrack's proto-ft stores them."""
    yaw = -pose.yaw * _D2R
    roll = pose.roll * _D2R
    # Falcon BMS "bump" workaround from opentrack: pitch exactly at +90° is nudged to 89.86°.
    pitch_deg = 89.86 if abs(pose.pitch - 90.0) < 0.15 else pose.pitch
    pitch = -pitch_deg * _D2R
    return (yaw, pitch, roll, pose.x * 10.0, pose.y * 10.0, pose.z * 10.0)


def raw_values(raw: HeadPose) -> tuple:
    """(RawYaw, RawPitch, RawRoll, RawX, RawY, RawZ) as proto-ft stores them (note: raw pitch is NOT negated there)."""
    return (-raw.yaw * _D2R, raw.pitch * _D2R, raw.roll * _D2R, raw.x * 10.0, raw.y * 10.0, raw.z * 10.0)


class FreetrackMemory:
    """Writes `FTHeap` into any writable buffer supporting slice assignment (mmap or bytearray)."""

    def __init__(self, buf, games: Optional[Dict[int, game_table.GameEntry]] = None):
        if len(buf) < FTHEAP_SIZE:
            raise ValueError(f"buffer too small: {len(buf)} < {FTHEAP_SIZE}")
        self.buf = buf
        self.games = games
        self.game_id = -1
        self.game_name = ""
        self.data_id = 1

    def initialize(self) -> None:
        struct.pack_into("<Iii", self.buf, OFF_DATA_ID, 1, 100, 250)
        struct.pack_into("<i", self.buf, OFF_GAME_ID2, 0)
        struct.pack_into("<8s", self.buf, OFF_TABLE, bytes(8))
        self.data_id = 1

    def write(self, pose: HeadPose, raw: Optional[HeadPose] = None) -> None:
        struct.pack_into("<6f", self.buf, OFF_POSE, *ft_values(pose))
        struct.pack_into("<6f", self.buf, OFF_RAW, *raw_values(raw if raw is not None else pose))
        game_id = struct.unpack_from("<i", self.buf, OFF_GAME_ID)[0]
        if game_id != self.game_id:
            entry = game_table.lookup(game_id, self.games)
            struct.pack_into("<8s", self.buf, OFF_TABLE, entry.table)
            struct.pack_into("<i", self.buf, OFF_GAME_ID2, game_id)
            struct.pack_into("<I", self.buf, OFF_DATA_ID, 0)
            self.data_id = 0
            self.game_id = game_id
            self.game_name = entry.name if entry.name else "Unknown game"
        else:
            # the client DLL resets DataID above 2^29; re-read so we continue from its value
            current = struct.unpack_from("<I", self.buf, OFF_DATA_ID)[0]
            self.data_id = (current + 1) & 0xFFFFFFFF
            struct.pack_into("<I", self.buf, OFF_DATA_ID, self.data_id)

    def read_pose(self) -> tuple:
        return struct.unpack_from("<6f", self.buf, OFF_POSE)


class OutputUnavailable(RuntimeError):
    pass


def find_libs_dir(candidates=None) -> Optional[str]:
    """Directory holding the client DLLs (bundled next to the package or beside the exe)."""
    dirs = list(candidates or [])
    dirs.append(LIBS_DIR)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "libs"))
        dirs.append(os.path.join(getattr(sys, "_MEIPASS", ""), "headtrack_pc", "libs"))
    for d in dirs:
        if d and os.path.isfile(os.path.join(d, "NPClient.dll")) and os.path.isfile(os.path.join(d, "freetrackclient.dll")):
            return d
    return None


def registry_path_value(directory: str) -> str:
    """How opentrack writes the Path value: forward slashes and a trailing slash."""
    p = directory.replace("\\", "/")
    return p if p.endswith("/") else p + "/"


class FreetrackOutput:
    """Windows only. interface: "both" (default, like opentrack), "freetrack" or "npclient"."""

    def __init__(self, interface: str = "both", libs_dir: Optional[str] = None, start_dummy: bool = True):
        if sys.platform != "win32":
            raise OutputUnavailable("freetrack/TrackIR output needs Windows (games read a Windows file mapping)")
        if interface not in ("both", "freetrack", "npclient"):
            raise ValueError("interface must be both, freetrack or npclient")
        self.interface = interface
        self.libs_dir = libs_dir or find_libs_dir()
        if self.libs_dir is None:
            raise OutputUnavailable("Client DLLs not found. Run `python tools/fetch_opentrack_libs.py` to download them from the opentrack release.")
        self._mutex = None
        self._mmap = None
        self._dummy: Optional[subprocess.Popen] = None
        self._open_mapping()
        self.memory = FreetrackMemory(self._mmap)
        self.memory.initialize()
        self._set_registry()
        if start_dummy and interface != "freetrack":
            self._start_dummy()

    def _open_mapping(self) -> None:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateMutexA.restype = ctypes.c_void_p
        self._mutex = kernel32.CreateMutexA(None, False, MUTEX_NAME.encode("ascii"))
        self._mmap = mmap.mmap(-1, FTHEAP_SIZE, tagname=SHM_NAME, access=mmap.ACCESS_WRITE)

    def _set_registry(self) -> None:
        import winreg
        value = registry_path_value(self.libs_dir)
        use_ft = self.interface in ("both", "freetrack")
        use_np = self.interface in ("both", "npclient")
        for key, enabled in ((r"Software\Freetrack\FreetrackClient", use_ft),
                             (r"Software\NaturalPoint\NATURALPOINT\NPClient Location", use_np)):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
                winreg.SetValueEx(k, "Path", 0, winreg.REG_SZ, value if enabled else "")

    def _start_dummy(self) -> None:
        exe = os.path.join(self.libs_dir, DUMMY_EXE)
        if not os.path.isfile(exe):
            return
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._dummy = subprocess.Popen([exe], cwd=self.libs_dir, creationflags=flags)
        except OSError:
            self._dummy = None

    @property
    def game_name(self) -> str:
        return self.memory.game_name

    @property
    def game_id(self) -> int:
        return self.memory.game_id

    def write(self, pose: HeadPose, raw: Optional[HeadPose] = None) -> None:
        self.memory.write(pose, raw)

    def close(self) -> None:
        try:
            self.memory.write(HeadPose(0.0, 0.0, 0.0))
        except Exception:
            pass
        if self._dummy is not None:
            try:
                self._dummy.kill()
                self._dummy.wait(timeout=1.0)
            except Exception:
                pass
            self._dummy = None
        if self._mmap is not None:
            try:
                self._mmap.close()
            except Exception:
                pass
            self._mmap = None
        if self._mutex:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self._mutex))  # type: ignore[attr-defined]
            except Exception:
                pass
            self._mutex = None
