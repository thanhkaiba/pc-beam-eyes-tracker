# Test report

| Date | What | Where | Result |
| --- | --- | --- | --- |
| 2026-10-09 | `python -m unittest discover -s tests -t .` (pose maths incl. camera-offset removal, calibration, mapping, filters, face-loss, pipeline, profile codec, packet codecs incl. discovery, phone receiver + ping + discovery over loopback, discovery responder, UDP output, FT_SharedMem layout/handshake, game table, engine with fake source/outputs) | Linux, Python 3.13, mediapipe 1.1.0 installed but not exercised | **68 passed** |
| 2026-10-09 | Tkinter window under Xvfb with a fake camera: centre step → calibration → tabs, Apply on every Advanced section, Connect tab text | Linux, Python 3.12 | passed (smoke) |
| 2026-10-09 | GitHub Actions `windows` job, run 3 (`.github/workflows/windows.yml`): 74 tests incl. the 6 Windows-only ones; CLI smoke with phone source and game output on; CLI smoke with webcam source on a runner without a camera (no crash, stays *Starting* for the 3 s window); Tk GUI smoke with a fake camera and game output on (centre set, tabs shown, every Apply); PyInstaller build; `HeadTrackPC-console.exe --version` and `--cli` run; `HeadTrackPC-windows.zip` (114 MB) uploaded as an artifact | windows-latest (Windows Server 2025), Python 3.12.10, mediapipe 1.1.0 | **all green** |
| 2026-10-09 | Game-side read-back on Windows: opentrack's real `freetrackclient64.dll` (`FTGetData`) returns the yaw/pitch/roll/x/y/z we wrote with the proto-ft signs and units, DataID increments; real `NPClient64.dll`: `NP_RegisterProgramProfileID(4525)` → our writer resolves *Beamng.drive*, echoes GameID2 and the table, `NP_GetData` returns the TrackIR-scaled angles (±16383/180° per degree) with the expected signs; registry `Path` values point at the DLL directory; dummy `TrackIR.exe` starts for the TrackIR interface and is killed on close; a second writer sees the same mapping | windows-latest | **passed** (`tests/test_windows_freetrack.py`) |
| 2026-10-09 | Features 1–5 (per-game profiles, hotkeys + automatic centre, direction check, eye-assisted look, diagnostics with fixes): 103 tests on Linux incl. engine sweep/eye/auto-centre/gaze-calibration and App game switching; GUI smoke covers preset, recenter, eye apply, self-check rows and a running sweep | Linux | **passed** |
| — | Hotkeys with a real keyboard/wheel, firewall fix (UAC), camera privacy page | Windows | **not run** (readers are ctypes calls, logic tested with fakes) |
| — | Webcam + MediaPipe on a real camera (runners have none) | Windows PC with webcam | **not run** |
| — | A real game reading the pose in-game (what the DLL round trip simulates) | Windows + game | **not run** |
| — | Phone finds the PC and drives the game over Wi-Fi | Windows + Android | **not run** |
| — | Steamworks init with the real `steam_api64.dll` (SDK not redistributable here; `tests/test_steam.py` covers the logic with a fake DLL) | Steam client | **not run** |

The MediaPipe Python API used (`FaceLandmarkerOptions(... running_mode=LIVE_STREAM, output_facial_transformation_matrixes=True, result_callback=...)`,
`FaceLandmarker.create_from_options`, `detect_async(image, ts_ms)`, `result.facial_transformation_matrixes` as 4×4 numpy arrays) was checked by
introspection against mediapipe 1.1.0 on 2026-10-09; it is identical to the 0.10.x API the model card documents.
