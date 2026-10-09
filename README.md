# HeadTrack PC — webcam (or phone) head tracking straight into Windows games

Webcam → head pose → **the game**, with no opentrack to install: this program does what
opentrack's *freetrack 2.0 Enhanced* output does (shared memory + the client DLLs games load) by
itself. It is the PC counterpart of the Android app (`android-beam-eyes-tracker`): same pose
convention, same calibration, mapping and smoothing, same packet formats. The phone can send to
it instead of to opentrack, and the Connect tab on the phone finds this PC by itself.

**Status (2026-10-09):** library, engine, protocols and the game output are implemented; 82 tests
pass (68 portable + 6 Windows-only + 8 Steam) and the GitHub Actions `windows` job is green: on a
Windows runner, opentrack's real `NPClient64.dll` and `freetrackclient64.dll` read our pose back
correctly, the Tk window completes the centre step with a fake camera, PyInstaller builds
`HeadTrackPC.exe` and the built exe runs. **Not yet run: a real webcam, a real game and a phone
over Wi-Fi** (runners have none of them); see `docs/test-report.md`. Optional Steamworks
integration and SteamPipe files are in `headtrack_pc/steam.py`, `steam/` and `docs/steam.md`.

## How it fits together

```
 webcam ──► MediaPipe Face Landmarker ──► pose ──► centre ──► mapping ──► One Euro ──► face-loss ──┐
                                                                                                    ├─► FT_SharedMem ──► NPClient.dll / freetrackclient.dll ──► game
 phone  ──► UDP 4242 (48/72-byte packets, already mapped on the phone) ──► stale check ───────────┘
            UDP 4244 discovery: the phone broadcasts "HTDQ", this PC answers with its name          └─► optional UDP to an opentrack
```

- `headtrack_pc/pose.py`, `calibration.py`, `mapping.py`, `filters.py`, `faceloss.py`,
  `pipeline.py`, `profile.py` — ports of the Android `core` library (pure Python, tested).
- `protocol.py` — the opentrack 48-byte packet, the 72-byte HeadTrack packet, pings and the
  discovery packets (`docs/discovery.md`).
- `inputs/webcam.py` (OpenCV + MediaPipe, live-stream mode), `inputs/phone.py` (receiver on
  port 4242 that also answers pings and discovery).
- `outputs/freetrack.py` — the game output (`docs/game-output.md`), `outputs/udp.py` — to opentrack.
- `engine.py` — one worker thread: frames in, pipeline, outputs, idle resend, calibration flow,
  phone-overrides-webcam policy. `app.py` wires it from the profile; `gui.py` (Tkinter) and
  `cli.py` sit on top.

## What it does that opentrack does not

- **No setup**: no input/output/filter plug-ins to pick, no opentrack install; the game output
  is built in and the centre is set in one click at every launch.
- **Per-game tuning, automatic**: when a game's DLL connects, its own tuning loads (a *driving*
  or *flight* preset the first time), and edits made while it runs are saved for that game.
- **Recenter from inside the game**: a global hotkey (F12 by default) or a wheel/joystick button,
  plus an automatic centre that follows your resting posture over a long session without ever
  jumping or reacting to a deliberate look aside.
- **Direction check**: a cockpit preview that moves like a driving game's camera, and a 25 s
  one-axis-at-a-time sweep through the real pipeline, so wrong signs are found before the race.
- **Eye-assisted look** (experimental): a glance toward a mirror adds yaw so the eyes can stay on
  the screen; optional head-turn compensation calibrated in 6 s.
- **Diagnostics that say what to do**: camera busy, no face, missing DLLs, game not loading the
  tracker, phone port taken by opentrack, firewall rule missing, each with a fix button.
- **Phone or webcam, or both**: the Android app finds this PC by itself, its link is confirmed by
  pings, and the webcam takes over when the phone stops.

## Install (Windows, Python 3.10–3.13)

```powershell
git clone <this repo> && cd pc-beam-eyes-tracker
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python tools\fetch_models.py           # MediaPipe face_landmarker.task (Apache 2.0, ~3.7 MB)
python tools\fetch_opentrack_libs.py   # NPClient*.dll, freetrackclient*.dll, TrackIR.exe from opentrack's repo (sha256-pinned)
python -m unittest discover -s tests -t .
python -m headtrack_pc                 # the window;  python -m headtrack_pc --cli  for a console view
```

`scripts\build.ps1` does all of the above and builds `dist\HeadTrackPC\HeadTrackPC.exe` (window) and
`HeadTrackPC-console.exe` (console, for `--cli`) with PyInstaller. The GitHub Actions workflow
`.github/workflows/windows.yml` runs the same on a Windows runner, including a round trip through
the real `NPClient64.dll` / `freetrackclient64.dll`, and uploads `HeadTrackPC-windows.zip`. On Linux/macOS everything but the game output works (useful for development and for
the UDP output to an opentrack).

## Use

1. Start HeadTrack PC. Allow it through Windows Firewall on private networks when asked (UDP
   4242 and 4244) if you will use the phone.
2. **Set your centre**: sit as you play, look at the screen, click *Calibrate centre*. Like the
   Android app this is asked every launch. The tabs appear once the centre is set.
3. Start the game and enable TrackIR / FreeTrack head tracking in its options. The status bar
   shows the game's name once its DLL connects. *Recenter* on the Track tab whenever you shift.
4. **Phone instead of webcam**: on the phone, Connect tab → *Find PC on this network* → tap this
   PC → Connect. While the phone sends, it drives the game and the webcam is ignored; when it
   stops, the webcam takes over again. *Advanced → Camera → Phone only* turns the webcam off.
5. **Advanced**: game output on/off and TrackIR-only / FreeTrack-only, an extra UDP output to an
   opentrack (for games that only work with opentrack's other protocols), camera index and
   mirroring, per-axis tuning (same meaning as the Android app's), diagnostics with a copyable
   report.

## Verifying on a Windows PC

See `docs/windows-setup.md` for the step list and `docs/test-report.md` for what each step
proves. In short: `python -m headtrack_pc --cli` shows raw and sent angles; `tools/w2` from the
Android repo can point at this PC's UDP output; the game check is the one that matters.

## Licensing

HeadTrack PC is open source under the MIT licence (`LICENSE`). It redistributes opentrack's
client binaries and game table (ISC) and uses MediaPipe (Apache 2.0): see
`THIRD_PARTY_NOTICES.md`. The companion Android app is a separate, closed-source product; this
repository documents the protocols it speaks (`docs/discovery.md`, the opentrack 48-byte and
HeadTrack 72-byte packets) so any sender can interoperate.
