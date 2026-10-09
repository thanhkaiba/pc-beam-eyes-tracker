# HeadTrack PC — webcam (or phone) head tracking straight into Windows games

Webcam → head pose → **the game**, with no opentrack to install: this program does what
opentrack's *freetrack 2.0 Enhanced* output does (shared memory + the client DLLs games load) by
itself. It is the PC counterpart of the Android app (`android-beam-eyes-tracker`): same pose
convention, same calibration, mapping and smoothing, same packet formats. The phone can send to
it instead of to opentrack, and the Connect tab on the phone finds this PC by itself.

**Status (2026-10-09):** library, engine, protocols and the game-output logic are implemented with
68 unit/loopback tests passing on Linux (the layout of the shared memory is checked byte by byte
against opentrack's `fttypes.h`). **Nothing has run on a Windows PC with a webcam or a game yet.**
The two things only a Windows PC can verify are listed in `docs/test-report.md`.

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

This project's own code is unlicensed-as-yet (same as the Android repo). It redistributes
opentrack binaries and its game table (ISC) and uses MediaPipe (Apache 2.0): see
`THIRD_PARTY_NOTICES.md`.
