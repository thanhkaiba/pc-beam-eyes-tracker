"""Live self-check (Help → Diagnostics): camera → face → centre → game output → game →
phone → discovery → firewall, in the order a user fixes things, each row with a plain-words
detail and, where the app can help, a fix action id handled by `fixes.py`. Pure function."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class CheckResult(Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class CheckItem:
    id: str
    title: str
    result: CheckResult
    detail: str
    fix: Optional[str] = None       # fixes.py action id
    fix_label: Optional[str] = None


@dataclass(frozen=True)
class SelfCheckInput:
    windows: bool = True
    webcam_wanted: bool = True
    webcam_status: str = "Stopped"   # SourceStatus.value
    webcam_error: Optional[str] = None
    face_detected: bool = False
    fps: float = 0.0
    seconds_since_webcam_start: float = 0.0
    has_neutral: bool = False
    game_output_wanted: bool = True
    game_output_active: bool = False
    game_output_error: Optional[str] = None
    libs_present: bool = True
    game_id: int = 0
    game_name: str = ""
    udp_output: bool = False
    phone_status: str = "Stopped"
    phone_error: Optional[str] = None
    phone_packets: int = 0
    phone_fresh: bool = False
    discovery_on: bool = False
    discovery_error: Optional[str] = None
    discoveries_answered: int = 0
    firewall_rule_present: Optional[bool] = None  # None = unknown
    sweeping: bool = False


@dataclass(frozen=True)
class SelfCheckReport:
    items: List[CheckItem]
    headline: str

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if i.result is CheckResult.FAIL)

    @property
    def warned(self) -> int:
        return sum(1 for i in self.items if i.result is CheckResult.WARN)

    @property
    def checked(self) -> int:
        return sum(1 for i in self.items if i.result is not CheckResult.SKIP)


GOOD_FPS = 15.0
LOW_FPS = 8.0


def run(i: SelfCheckInput) -> SelfCheckReport:
    items: List[CheckItem] = []

    def add(id_, title, result, detail, fix=None, fix_label=None):
        items.append(CheckItem(id_, title, result, detail, fix, fix_label))

    # 1. Camera
    err = (i.webcam_error or "").lower()
    if not i.webcam_wanted:
        add("camera", "Camera", CheckResult.SKIP, "Phone-only mode: the webcam is off")
    elif i.sweeping:
        add("camera", "Camera", CheckResult.SKIP, "Replaced by the direction check for now")
    elif i.webcam_status == "Failed" and ("another program" in err or "could not be opened" in err):
        add("camera", "Camera", CheckResult.FAIL, "The camera could not be opened: another program (Zoom, Teams, OBS, a browser tab) may be using it, or Windows camera access is off for desktop apps.",
            fix="camera_privacy", fix_label="Open camera settings")
    elif i.webcam_status == "Failed" and "model" in err:
        add("camera", "Camera", CheckResult.FAIL, "The face model file is missing.", fix="fetch_model", fix_label="Download model")
    elif i.webcam_status == "Failed":
        add("camera", "Camera", CheckResult.FAIL, i.webcam_error or "Camera failed", fix="retry_camera", fix_label="Retry camera")
    elif i.webcam_status == "Running":
        add("camera", "Camera", CheckResult.PASS, f"Webcam delivering frames ({i.fps:.0f} fps)")
    elif i.webcam_status == "Starting" and i.seconds_since_webcam_start > 8:
        add("camera", "Camera", CheckResult.WARN, "Still starting after 8 s: Windows may be waiting on the camera driver, or no camera is connected.", fix="retry_camera", fix_label="Retry camera")
    else:
        add("camera", "Camera", CheckResult.WARN, "Camera starting…")
    # 2. Face
    if not i.webcam_wanted or i.sweeping:
        add("face", "Face", CheckResult.SKIP, "Not using the webcam")
    elif i.face_detected:
        add("face", "Face", CheckResult.PASS, "Face tracked")
    elif i.webcam_status == "Running":
        add("face", "Face", CheckResult.FAIL, "No face in view: sit 40–80 cm from the camera with light on your face, not behind you")
    else:
        add("face", "Face", CheckResult.SKIP, "Needs the camera")
    # 3. Rate
    if not i.face_detected or i.sweeping:
        add("rate", "Tracking rate", CheckResult.SKIP, "Needs a tracked face")
    elif i.fps >= GOOD_FPS:
        add("rate", "Tracking rate", CheckResult.PASS, f"{i.fps:.0f} fps")
    elif i.fps >= LOW_FPS:
        add("rate", "Tracking rate", CheckResult.WARN, f"{i.fps:.0f} fps: usable but laggy. Close other camera apps; lower the camera resolution in Settings → Camera")
    else:
        add("rate", "Tracking rate", CheckResult.FAIL, f"{i.fps:.0f} fps: too slow. Close other apps using the CPU, or lower the camera resolution")
    # 4. Centre
    if i.has_neutral:
        add("centre", "Centre", CheckResult.PASS, "Centre set: the game receives your head relative to it")
    elif i.phone_fresh or not i.webcam_wanted:
        add("centre", "Centre", CheckResult.SKIP, "The phone sends already-centred poses")
    else:
        add("centre", "Centre", CheckResult.FAIL, "No centre: the game receives a neutral pose. Home → Recalibrate")
    # 5. Game output
    if not i.game_output_wanted:
        add("output", "Game output", CheckResult.SKIP if i.udp_output else CheckResult.WARN,
            "Off (UDP to opentrack is on)" if i.udp_output else "Off, and no UDP output either: nothing reaches a game")
    elif not i.windows:
        add("output", "Game output", CheckResult.SKIP, "Needs Windows")
    elif not i.libs_present:
        add("output", "Game output", CheckResult.FAIL, "The client DLLs games load are missing.", fix="fetch_libs", fix_label="Download DLLs")
    elif i.game_output_error:
        add("output", "Game output", CheckResult.FAIL, i.game_output_error)
    elif i.game_output_active:
        add("output", "Game output", CheckResult.PASS, "freetrack / TrackIR shared memory active; DLL path registered")
    else:
        add("output", "Game output", CheckResult.WARN, "Not active yet")
    # 6. Game
    if not i.game_output_wanted or not i.windows:
        add("game", "Game", CheckResult.SKIP, "No game output")
    elif i.game_id > 0:
        add("game", "Game", CheckResult.PASS, f"{i.game_name or 'A game'} loaded the tracker DLL (ID {i.game_id})")
    else:
        add("game", "Game", CheckResult.WARN, "No game has loaded the tracker yet. Start the game and enable TrackIR / FreeTrack in its options; if it stays here, the game may block unsigned DLLs (anti-cheat) or opentrack may be running at the same time.")
    # 7. Phone port
    if i.phone_status == "Running":
        if i.phone_fresh:
            add("phone", "Phone", CheckResult.PASS, "Phone connected and driving the game")
        elif i.phone_packets > 0:
            add("phone", "Phone", CheckResult.PASS, f"Phone port open; {i.phone_packets} packets so far (phone idle now)")
        else:
            add("phone", "Phone", CheckResult.PASS, "Phone port open, no phone connected (fine when using the webcam)")
    elif i.phone_status == "Failed":
        perr = i.phone_error or ""
        add("phone", "Phone", CheckResult.WARN, perr + (" opentrack's UDP input uses the same port: close opentrack, or change the port in the profile." if "listen" in perr.lower() else ""))
    else:
        add("phone", "Phone", CheckResult.SKIP, "Phone receiver off")
    # 8. Discovery
    if i.discovery_error:
        add("discovery", "Phone discovery", CheckResult.WARN, i.discovery_error)
    elif i.discovery_on:
        add("discovery", "Phone discovery", CheckResult.PASS, f"Answering the phone's search ({i.discoveries_answered} answered)")
    else:
        add("discovery", "Phone discovery", CheckResult.SKIP, "Off")
    # 9. Firewall
    if not i.windows:
        add("firewall", "Windows Firewall", CheckResult.SKIP, "Not Windows")
    elif i.firewall_rule_present is True:
        add("firewall", "Windows Firewall", CheckResult.PASS, "Inbound rule for HeadTrack PC present")
    elif i.firewall_rule_present is False:
        add("firewall", "Windows Firewall", CheckResult.WARN, "No inbound rule for this program: the phone cannot find or reach this PC until Windows allows UDP 4242/4244 in.",
            fix="firewall", fix_label="Allow in firewall (admin)")
    else:
        add("firewall", "Windows Firewall", CheckResult.SKIP, "Unknown (could not query the firewall)")

    report = SelfCheckReport(items, "")
    if report.failed > 0:
        headline = f"{report.failed} of {report.checked} checks failed"
    elif report.warned > 0:
        headline = f"{report.warned} of {report.checked} checks need attention"
    else:
        headline = f"All {report.checked} checks passed"
    return SelfCheckReport(items, headline)
