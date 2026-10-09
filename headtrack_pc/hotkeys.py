"""Global hotkey (keyboard key or joystick/wheel button) that triggers Recenter from inside a game.

Polling (`GetAsyncKeyState`, `joyGetPosEx`) rather than `RegisterHotKey`: it needs no window or
message loop, works while a full-screen game has focus, and reads wheel buttons the same way.
The edge detection is pure so it is tested without Windows; the readers are injected.
"""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

VK: Dict[str, int] = {**{f"F{i}": 0x6F + i for i in range(1, 25)}, "SPACE": 0x20, "ENTER": 0x0D, "TAB": 0x09, "ESC": 0x1B,
                      "HOME": 0x24, "END": 0x23, "INSERT": 0x2D, "DELETE": 0x2E, "PAUSE": 0x13, "SCROLLLOCK": 0x91,
                      "NUMPAD0": 0x60, "NUMPAD5": 0x65, "NUMPAD_PLUS": 0x6B, "NUMPAD_MINUS": 0x6D, "NUMPAD_STAR": 0x6A,
                      "BACKSPACE": 0x08, "CAPSLOCK": 0x14, "NUMLOCK": 0x90}
for _c in "0123456789":
    VK[_c] = ord(_c)
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VK[_c] = ord(_c)


def parse_key(name: str) -> Optional[int]:
    """'F12', 'Space', 'a' → virtual-key code; None when unknown or empty."""
    key = name.strip().upper().replace(" ", "")
    if not key:
        return None
    return VK.get(key)


@dataclass(frozen=True)
class HotkeySettings:
    enabled: bool = True
    recenter_key: str = "F12"
    joystick_id: int = -1       # -1 = none; 0..15 = winmm joystick index
    joystick_button: int = -1   # -1 = none; 0-based button index
    poll_millis: int = 30


class EdgeDetector:
    """Fires once per press (false → true) for each source; a held key fires again only after release."""

    def __init__(self):
        self._down: Dict[str, bool] = {}

    def update(self, source: str, pressed: bool) -> bool:
        was = self._down.get(source, False)
        self._down[source] = pressed
        return pressed and not was


class KeyReader:
    def pressed(self, vk: int) -> bool:  # pragma: no cover - platform
        raise NotImplementedError


class JoystickReader:
    def buttons(self, joystick_id: int) -> int:  # pragma: no cover - platform
        raise NotImplementedError


class WindowsKeyReader(KeyReader):
    def __init__(self):
        import ctypes
        self._fn = ctypes.windll.user32.GetAsyncKeyState  # type: ignore[attr-defined]
        self._fn.restype = ctypes.c_short

    def pressed(self, vk: int) -> bool:
        return bool(self._fn(vk) & 0x8000)


class WindowsJoystickReader(JoystickReader):
    def __init__(self):
        import ctypes

        class JOYINFOEX(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32), ("dwXpos", ctypes.c_uint32),
                        ("dwYpos", ctypes.c_uint32), ("dwZpos", ctypes.c_uint32), ("dwRpos", ctypes.c_uint32),
                        ("dwUpos", ctypes.c_uint32), ("dwVpos", ctypes.c_uint32), ("dwButtons", ctypes.c_uint32),
                        ("dwButtonNumber", ctypes.c_uint32), ("dwPOV", ctypes.c_uint32), ("dwReserved1", ctypes.c_uint32),
                        ("dwReserved2", ctypes.c_uint32)]
        self._info = JOYINFOEX()
        self._info.dwSize = ctypes.sizeof(JOYINFOEX)
        self._info.dwFlags = 0x80  # JOY_RETURNBUTTONS
        self._fn = ctypes.windll.winmm.joyGetPosEx  # type: ignore[attr-defined]
        self._ctypes = ctypes

    def buttons(self, joystick_id: int) -> int:
        if self._fn(joystick_id, self._ctypes.byref(self._info)) != 0:
            return 0
        return int(self._info.dwButtons)


class HotkeyPoller:
    def __init__(self, settings: HotkeySettings, on_recenter: Callable[[], None],
                 keys: Optional[KeyReader] = None, joysticks: Optional[JoystickReader] = None):
        self.settings = settings
        self._on_recenter = on_recenter
        self._keys = keys
        self._joy = joysticks
        self._edge = EdgeDetector()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.error: Optional[str] = None
        self.fired = 0

    def start(self) -> None:
        if self._keys is None or self._joy is None:
            if sys.platform != "win32":
                self.error = "Hotkeys need Windows"
                return
            try:
                self._keys = self._keys or WindowsKeyReader()
                self._joy = self._joy or WindowsJoystickReader()
            except Exception as e:  # pragma: no cover - platform
                self.error = f"Hotkeys unavailable: {e}"
                return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()

    def poll_once(self) -> bool:
        """One poll; returns True when a recenter was triggered (tests call this directly)."""
        s = self.settings
        if not s.enabled:
            return False
        fired = False
        vk = parse_key(s.recenter_key)
        if vk is not None and self._keys is not None:
            try:
                fired |= self._edge.update("key", self._keys.pressed(vk))
            except Exception:
                pass
        if s.joystick_id >= 0 and s.joystick_button >= 0 and self._joy is not None:
            try:
                mask = self._joy.buttons(s.joystick_id)
                fired |= self._edge.update("joy", bool(mask & (1 << s.joystick_button)))
            except Exception:
                pass
        if fired:
            self.fired += 1
            self._on_recenter()
        return fired

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            time.sleep(max(0.01, self.settings.poll_millis / 1000.0))

    def update(self, settings: HotkeySettings) -> None:
        self.settings = settings

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=1.0)
