"""Fix actions behind the Diagnostics rows. Each returns a short message for the UI."""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Dict, Optional

RULE_NAME = "HeadTrack PC"


def program_path() -> str:
    return sys.executable if getattr(sys, "frozen", False) else sys.executable


def firewall_rule_present() -> Optional[bool]:
    """True/False from `netsh advfirewall firewall show rule`, None off Windows or on error."""
    if sys.platform != "win32":
        return None
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE_NAME}"],
                             capture_output=True, text=True, timeout=5, creationflags=flags)
        return out.returncode == 0 and "Rule Name" in out.stdout
    except Exception:
        return None


def add_firewall_rule() -> str:
    """Adds inbound UDP 4242/4244 rules for this program, elevated (UAC prompt)."""
    if sys.platform != "win32":
        return "Firewall rules are a Windows feature"
    exe = program_path()
    cmd = (f'netsh advfirewall firewall add rule name="{RULE_NAME}" dir=in action=allow protocol=UDP localport=4242,4244 '
           f'program="{exe}" profile=private,domain enable=yes')
    try:
        import ctypes
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f"/c {cmd}", None, 0)  # type: ignore[attr-defined]
        return "Rule added (if you approved the prompt)" if r > 32 else f"Could not start the elevated command (code {r})"
    except Exception as e:
        return f"Could not add the rule: {e}"


def open_camera_privacy() -> str:
    if sys.platform != "win32":
        return "Camera privacy settings are a Windows feature"
    try:
        os.startfile("ms-settings:privacy-webcam")  # type: ignore[attr-defined]
        return "Opened Windows camera settings: allow desktop apps to access the camera"
    except Exception as e:
        return f"Could not open settings: {e}"


def fetch_libs() -> str:
    try:
        from tools import fetch_opentrack_libs  # type: ignore
    except ImportError:
        import importlib.util
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location("fetch_opentrack_libs", os.path.join(here, "tools", "fetch_opentrack_libs.py"))
        if spec is None or spec.loader is None:
            return "Downloader not found next to the program"
        fetch_opentrack_libs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fetch_opentrack_libs)
    try:
        return "DLLs downloaded; turn the game output off and on" if fetch_opentrack_libs.main([]) == 0 else "Download failed (see log)"
    except Exception as e:
        return f"Download failed: {e}"


def fetch_model() -> str:
    try:
        import importlib.util
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location("fetch_models", os.path.join(here, "tools", "fetch_models.py"))
        if spec is None or spec.loader is None:
            return "Downloader not found next to the program"
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return "Model downloaded; retry the camera" if m.main() == 0 else "Download failed"
    except Exception as e:
        return f"Download failed: {e}"


ACTIONS: Dict[str, Callable[[], str]] = {
    "firewall": add_firewall_rule,
    "camera_privacy": open_camera_privacy,
    "fetch_libs": fetch_libs,
    "fetch_model": fetch_model,
}


def run(action: str, extra: Optional[Dict[str, Callable[[], str]]] = None) -> str:
    table = dict(ACTIONS)
    if extra:
        table.update(extra)
    fn = table.get(action)
    return fn() if fn else f"Unknown action {action}"
