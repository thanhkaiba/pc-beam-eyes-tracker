# Windows set-up and first checks

## Install
See README "Install". The fetch scripts need internet once; after that nothing does.

## Check 1 — webcam tracking (no game)
`python -m headtrack_pc --cli` → after 2 s it calibrates by itself; the `raw=` angles must follow
your head (turn right → yaw positive; look up → pitch positive; right ear down → roll positive).
If yaw/roll are reversed your camera driver mirrors the image: *Advanced → Camera → mirrored*.

## Check 2 — the game sees the tracker
1. Start HeadTrack PC with the default output (both interfaces). *Advanced → Diagnostics* must say
   `game_output=yes`; if it says the DLLs are missing, run `tools\fetch_opentrack_libs.py`.
2. Start the game, enable TrackIR (or FreeTrack) in its options.
3. The status bar should show *Game: <name>* within a second (the game's DLL wrote its ID).
   No name → the game did not load the DLL: check the registry values exist
   (`reg query HKCU\Software\NaturalPoint\NATURALPOINT\NPClient Location`), that the game is not
   blocked by an anti-cheat, and that no opentrack is running at the same time (two writers).
4. Turn your head; the in-game camera must follow. Reversed axis → *Invert* for that axis in Advanced.

## Check 3 — the phone finds this PC
Phone on the same Wi-Fi, HeadTrack app → Connect → *Find PC on this network*. This PC must appear
with its name. If not: Windows Firewall (allow the Python/HeadTrackPC executable on private networks
or add inbound UDP 4244 and 4242), or the router isolates Wi-Fi clients ("AP isolation").
Then tap it and Connect: the Connect tab on the PC shows the packet counter and *Fresh: the phone
drives the game*.

## Check 4 — fall-back to opentrack
If a game refuses our DLLs, *Advanced → Also send UDP to opentrack at 127.0.0.1:4242* and use
opentrack as before (its *UDP over network* input). The Android repo's `tools/w2/opentrack_w2_check.py`
works unchanged against that stream.
