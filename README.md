# HeadTrack PC

Head and eye tracking for games, with a webcam or your phone. No opentrack needed.

Free and open source (MIT). Windows 10/11.

## Install

1. Download `HeadTrackPC-Setup.exe` from the [latest release](https://github.com/thanhkaiba/pc-beam-eyes-tracker/releases/latest) and run it.
2. Sit as you play, look at the screen, press **Calibrate centre**.
3. Start your game and turn on TrackIR or FreeTrack in its options. Done.

Windows may warn that the file is unsigned: *More info → Run anyway*.

## What you get

- Works with every game that supports TrackIR or FreeTrack (745 titles listed in the app).
- Head mouse for games without head-tracking support.
- Eye tracking: look at 9 dots once, then your eyes move the game camera too.
- Phone as camera: the Android app finds this PC by itself over Wi-Fi.
- Per-game settings, switched automatically. Driving and flight presets.
- Recenter with F12 or a wheel button. Pause with F11.
- Streaming overlay for OBS and a local JSON API at `http://127.0.0.1:4245/`.

## Help

Something not working? Open **Help → Diagnostics**: each line says what is wrong and has a fix button.

More: [Windows setup](docs/windows-setup.md) · [API](docs/api.md) · [How the game output works](docs/game-output.md) · [Architecture](docs/architecture.md) · [Tests](docs/test-report.md)

## Developers

```
pip install -r requirements-dev.txt
python tools/fetch_models.py
python tools/fetch_opentrack_libs.py
python tools/fetch_assets.py     # optional: icons and the road photo
python -m headtrack_pc
```

Tests: `python -m unittest discover -s tests -t .` · Build: `scripts\build.ps1`

## Licence

MIT. Uses opentrack's client DLLs (ISC) and MediaPipe (Apache 2.0): see `THIRD_PARTY_NOTICES.md`.
