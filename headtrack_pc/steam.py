"""Optional Steamworks integration through the flat C API of `steam_api64.dll` (ctypes).

Everything is best-effort: without the DLL next to the executable (a plain zip or a dev checkout)
or without an app ID the program runs exactly as before. With both, at start-up it:

1. calls `SteamAPI_RestartAppIfNecessary(app_id)`: when the exe was launched outside Steam, Steam
   relaunches it through the client and we exit (so DRM/overlay/licensing behave);
2. calls `SteamAPI_InitFlat` (SDK ≥ 1.57) or `SteamAPI_Init` (older SDKs);
3. reads the persona name and app ID for the About section;
4. pumps `SteamAPI_RunCallbacks` from the GUI timer and calls `SteamAPI_Shutdown` on exit.

The SDK itself (steam_api64.dll) is not in this repository: it is downloaded from the Steamworks
partner site under its own licence and copied next to the exe by `scripts/steam_upload.ps1`.
"""
from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from typing import Callable, Optional

APP_ID_FILE = "steam_appid.txt"
DLL_NAMES = ("steam_api64.dll", "steam_api.dll")
FRIENDS_VERSIONS = ("SteamAPI_SteamFriends_v017", "SteamAPI_SteamFriends_v018")
UTILS_VERSIONS = ("SteamAPI_SteamUtils_v010", "SteamAPI_SteamUtils_v011")
INIT_OK = 0  # k_ESteamAPIInitResult_OK


@dataclass
class SteamStatus:
    available: bool = False          # a steam_api DLL was found
    initialized: bool = False        # SteamAPI_Init succeeded
    restart_requested: bool = False  # Steam will relaunch us; exit now
    app_id: int = 0
    persona_name: str = ""
    message: str = "not running under Steam"


def exe_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_app_id(directory: str) -> int:
    """App ID from steam_appid.txt (dev builds) or the HEADTRACK_STEAM_APPID variable; 0 if none."""
    env = os.environ.get("HEADTRACK_STEAM_APPID", "").strip()
    if env.isdigit():
        return int(env)
    try:
        with open(os.path.join(directory, APP_ID_FILE), "r", encoding="utf-8") as f:
            text = f.read().strip()
        return int(text) if text.isdigit() else 0
    except OSError:
        return 0


def find_dll(directory: str) -> Optional[str]:
    for name in DLL_NAMES:
        p = os.path.join(directory, name)
        if os.path.isfile(p):
            return p
    return None


class Steam:
    """One instance per process. `loader` is replaceable for tests (returns a ctypes-like object)."""

    def __init__(self, app_id: int = 0, directory: Optional[str] = None,
                 loader: Optional[Callable[[str], object]] = None):
        self.directory = directory or exe_dir()
        self.app_id = app_id or read_app_id(self.directory)
        self._loader = loader or (lambda path: ctypes.CDLL(path))
        self._lib = None
        self.status = SteamStatus(app_id=self.app_id)

    def start(self) -> SteamStatus:
        s = self.status
        path = find_dll(self.directory)
        if path is None:
            s.message = "steam_api64.dll not found: not running under Steam"
            return s
        if not self.app_id:
            s.message = f"{os.path.basename(path)} found but no app ID ({APP_ID_FILE})"
            return s
        try:
            lib = self._loader(path)
        except OSError as e:
            s.message = f"could not load {os.path.basename(path)}: {e}"
            return s
        self._lib = lib
        s.available = True
        try:
            restart = getattr(lib, "SteamAPI_RestartAppIfNecessary")
            restart.restype = ctypes.c_bool
            restart.argtypes = [ctypes.c_uint32]
            if restart(self.app_id):
                s.restart_requested = True
                s.message = "launched outside Steam: Steam is restarting the app"
                return s
        except AttributeError:
            pass
        if not self._init(lib):
            return s
        s.initialized = True
        s.message = "Steam connected"
        s.persona_name = self._persona_name(lib)
        s.app_id = self._app_id(lib) or self.app_id
        if s.persona_name:
            s.message = f"Steam connected as {s.persona_name}"
        return s

    def _init(self, lib) -> bool:
        s = self.status
        try:
            init_flat = getattr(lib, "SteamAPI_InitFlat")
        except AttributeError:
            init_flat = None
        if init_flat is not None:
            err = ctypes.create_string_buffer(1024)
            init_flat.restype = ctypes.c_int
            init_flat.argtypes = [ctypes.c_char_p]
            result = init_flat(err)
            if result != INIT_OK:
                s.message = f"SteamAPI_InitFlat failed ({result}): {err.value.decode('utf-8', 'replace')}"
                return False
            return True
        try:
            init = getattr(lib, "SteamAPI_Init")
        except AttributeError:
            s.message = "steam_api DLL has no SteamAPI_Init"
            return False
        init.restype = ctypes.c_bool
        if not init():
            s.message = "SteamAPI_Init failed (Steam not running or app not owned)"
            return False
        return True

    def _persona_name(self, lib) -> str:
        for accessor in FRIENDS_VERSIONS:
            try:
                get = getattr(lib, accessor)
                name_fn = getattr(lib, "SteamAPI_ISteamFriends_GetPersonaName")
            except AttributeError:
                continue
            get.restype = ctypes.c_void_p
            name_fn.restype = ctypes.c_char_p
            name_fn.argtypes = [ctypes.c_void_p]
            iface = get()
            if iface:
                raw = name_fn(iface)
                return raw.decode("utf-8", "replace") if raw else ""
        return ""

    def _app_id(self, lib) -> int:
        for accessor in UTILS_VERSIONS:
            try:
                get = getattr(lib, accessor)
                fn = getattr(lib, "SteamAPI_ISteamUtils_GetAppID")
            except AttributeError:
                continue
            get.restype = ctypes.c_void_p
            fn.restype = ctypes.c_uint32
            fn.argtypes = [ctypes.c_void_p]
            iface = get()
            if iface:
                return int(fn(iface))
        return 0

    def run_callbacks(self) -> None:
        if self._lib is not None and self.status.initialized:
            try:
                self._lib.SteamAPI_RunCallbacks()
            except Exception:
                pass

    def shutdown(self) -> None:
        if self._lib is not None and self.status.initialized:
            try:
                self._lib.SteamAPI_Shutdown()
            except Exception:
                pass
            self.status.initialized = False
        self._lib = None
