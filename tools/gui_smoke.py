#!/usr/bin/env python3
"""Opens the window with a fake camera, calibrates, visits every tab, presses every Apply, closes.
Exit code 0 when the centre step completed and the tabs appeared. Needs a display (Xvfb on Linux)."""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import headtrack_pc.app as appmod  # noqa: E402
from headtrack_pc.app import App  # noqa: E402
from headtrack_pc.engine import CalibrationPhase  # noqa: E402
from headtrack_pc.gui import HeadTrackWindow  # noqa: E402
from headtrack_pc.inputs.base import Frame, PoseSource, SourceStatus  # noqa: E402
from headtrack_pc.pose import HeadPose  # noqa: E402
from headtrack_pc.profile import DRIVING, OutputSettings, PhoneSettings  # noqa: E402


class FakeCam(PoseSource):
    def __init__(self, settings, **kw):
        self.status = SourceStatus.STOPPED
        self.error = None
        self._stop = threading.Event()

    def start(self, cb):
        self.status = SourceStatus.RUNNING

        def run():
            t = 0
            while not self._stop.is_set():
                t += 1
                now = time.monotonic_ns()
                cb(Frame(HeadPose(5.0 + 0.01 * (t % 2), -10.0, 1.0, 0, 0, -45, timestamp_nanos=now), now))
                time.sleep(0.033)
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self._stop.set()
        self.status = SourceStatus.STOPPED

    def preview(self):
        return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # degree signs on the Windows console
    except Exception:
        pass
    appmod.WebcamSource = FakeCam
    game_output = "--game-output" in sys.argv
    from headtrack_pc.profile import ApiSettings  # noqa: E402
    prof = replace(DRIVING, phone=PhoneSettings(track_port=24242, discovery_port=24244), api=ApiSettings(enabled=True, port=0),
                   output=OutputSettings(freetrack_enabled=game_output, udp_enabled=True, udp_host="127.0.0.1", udp_port=24243))
    app = App(prof, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "build", "smoke-profile.json"))
    win = HeadTrackWindow(app)
    app.start()
    result = {"ok": False, "log": []}

    def step1():
        result["log"].append(("status", win.status_var.get(), win.centre_hint.get()))
        app.calibrate()

    deadline = {"t": time.monotonic() + 25.0}

    def step2():
        st = app.engine.state
        # slow runners: wait for the centre step to finish (3 s countdown + 1 s sampling) instead of a fixed delay
        # also wait for the window's 50 ms poll to have moved from the centre step to the tabs
        if (st.calibration in (CalibrationPhase.COUNTDOWN, CalibrationPhase.SAMPLING, CalibrationPhase.IDLE)
                or (st.calibration is CalibrationPhase.DONE and not win.tabs.winfo_ismapped())) and time.monotonic() < deadline["t"]:
            win.root.after(200, step2)
            return
        result["log"].append(("calibration", st.calibration.value, st.calibration_message, win.tabs.winfo_ismapped()))
        result["ok"] = st.calibration is CalibrationPhase.DONE and bool(win.tabs.winfo_ismapped())
        win.tabs.select(win.advanced_tab)
        win._apply_tuning(); win._apply_output(); win._apply_camera(); win._apply_name()
        win._apply_preset("flight"); win._apply_recenter(); win._apply_eye(); win._update_checks()
        win.api_port.set("24245"); win._apply_api(); win._apply_mouse(); win._apply_extended(); win._fill_games()
        win.game_search.set("beam"); win._fill_games()
        result["log"].append(("api", win.api_msg.get(), win.games_list.size()))
        app.engine.toggle_pause(); app.engine.run_sync(lambda: None); app.engine.toggle_pause()
        result["log"].append(("tuning", win.tuning_for.get(), win.check_headline.get()))
        win.tabs.select(win.connect_tab)
        win.tabs.select(win.track_tab)
        win._toggle_sweep()

    def step3():
        result["log"].append(("sweep", win.sweep_var.get(), win.cockpit_words.get(), app.engine.sweeping))
        result["ok"] = result["ok"] and app.engine.sweeping and "Yaw" in win.sweep_var.get()
        win._toggle_sweep()
        result["log"].append(("connect", win.pc_var.get(), win.phone_var.get()))
        result["log"].append(("diag", win.diag_var.get()))
        result["log"].append(("outputs", win.output_notes.get()))
        win.close()

    win.root.after(800, step1)
    win.root.after(4500, step2)
    win.root.after(4800, lambda: step3_when_ready())

    def step3_when_ready():
        if "sweep" not in [l[0] for l in result["log"]] and any(l[0] == "tuning" for l in result["log"]):
            win.root.after(2500, step3)  # ~2.5 s into the sweep: the yaw-right phase
        else:
            win.root.after(300, step3_when_ready)
    win.root.mainloop()
    for line in result["log"]:
        print(line)
    print("GUI SMOKE", "OK" if result["ok"] else "FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
