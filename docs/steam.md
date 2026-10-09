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

## In-app Steamworks integration (`headtrack_pc/steam.py`)
When `steam_api64.dll` sits next to `HeadTrackPC.exe` (the upload script copies it there) the app
calls `SteamAPI_RestartAppIfNecessary` (so a launch outside Steam is redirected through Steam),
`SteamAPI_InitFlat`/`SteamAPI_Init`, shows "Steam connected as <name>" in Advanced → About, pumps
`SteamAPI_RunCallbacks` every 100 ms (overlay), and calls `SteamAPI_Shutdown` on exit. Without the
DLL nothing changes. `--no-steam` disables it. For local testing put `steam_appid.txt` with your
App ID next to the exe; the upload script deletes that file so it never ships.

## Upload (`scripts/steam_upload.ps1`)
1. Fill `YOUR_APP_ID` / `YOUR_DEPOT_ID` in `steam/app_build.vdf` and `steam/depot_build.vdf`.
2. Set `STEAMWORKS_SDK` to the unzipped SDK folder (needs `tools\ContentBuilder\builder\steamcmd.exe`
   and `redistributable_bin\win64\steam_api64.dll`).
3. `.\scripts\build.ps1` then `.\scripts\steam_upload.ps1 -Username <login>`; set the build live
   in Steamworks → SteamPipe → Builds. `steam/installscript.vdf` installs the VC++ runtime from
   Steam's common redistributables (add "Visual C++ 2022 Redist" to the depot in Steamworks).

## Not done here
Steamworks app ID and depot creation, store assets, code signing (unsigned exes trigger
SmartScreen outside Steam; inside Steam this is less of an issue), and an installer are outside
this repository. Nothing Steam-related has run against the real SDK (not redistributable here).
