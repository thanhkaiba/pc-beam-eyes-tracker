# Shipping on Steam: what the build has and what is still yours to decide

## What the repository produces
`.github/workflows/windows.yml` (or `scripts\build.ps1`) gives `dist\HeadTrackPC\`: a self-contained
folder with `HeadTrackPC.exe` (window), `HeadTrackPC-console.exe` (`--cli`, visible errors), the
MediaPipe model, the client DLLs and the game table. No Python install is needed on the player's PC.
That folder is the Steam depot content; `HeadTrackPC.exe` is the launch option.

## Needs on the player's PC
- Windows 10/11 x64. The MediaPipe wheel needs the Microsoft Visual C++ 2015-2022 runtime: add it as
  a Steam "common redistributable" in the app's install script so Steam installs it.
- A webcam, or the Android app on the same Wi-Fi.
- Windows Firewall prompt on first launch when the phone is used (UDP 4242/4244 inbound).

## Known limits to state on the store page
- Games must support TrackIR or FreeTrack. The same games opentrack works with (its game list is
  bundled). Games with anti-cheat that blocks unsigned DLLs will not work.
- Running opentrack at the same time conflicts (two writers of the same shared memory).

## Licensing to clear before publishing
- Your own code: pick and add a licence file (none in either repo yet).
- opentrack files (`freetrackclient*.dll`, `TrackIR.exe`, `games.csv`, and the protocol we
  re-implement): ISC, attribution kept in `THIRD_PARTY_NOTICES.md`; fine to redistribute.
- `NPClient*.dll`: opentrack ships it under `contrib/npclient`, credited to uglyDwarf (linuxtrack,
  MIT). It emulates NaturalPoint's TrackIR API. NaturalPoint has objected to TrackIR emulation in
  the past (FaceTrackNoIR, 2010). Distributing it commercially on Steam is a legal judgement call;
  the FreeTrack interface (`freetrackclient*.dll`) carries no such history, and the app can ship
  with *FreeTrack only* as default if you prefer, at the cost of games that only speak TrackIR.
- MediaPipe model and library: Apache 2.0, notice kept.
- PyInstaller: GPL with the bootloader exception, so a bundled closed-source app is allowed.

## Not done here
Steamworks app ID, depot/build scripts (`app_build.vdf`), store assets, code signing (unsigned
exes trigger SmartScreen outside Steam; inside Steam this is less of an issue), and an installer
are outside this repository.
