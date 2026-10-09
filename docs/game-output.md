# Game output: freetrack 2.0 / TrackIR without opentrack

Source of truth: opentrack 2026.1.0, `proto-ft/ftnoir_protocol_ft.cpp`, `proto-ft/ftnoir_protocol_ft.h`,
`freetrackclient/fttypes.h`, `freetrackclient/freetrackclient.c`, `csv/csv.cpp`, `compat/shm.cpp`
(all read on 2026-10-09). `headtrack_pc/outputs/freetrack.py` does the same four things.

## 1. Shared memory `FT_SharedMem` (108 bytes)

Page-file-backed file mapping (`CreateFileMappingA(INVALID_HANDLE_VALUE, …, PAGE_READWRITE, 0, 108, "FT_SharedMem")`;
Python: `mmap.mmap(-1, 108, tagname="FT_SharedMem")`), plus a named mutex `FT_Mutext` that the client
DLL takes while copying (the tracker writes without it, with interlocked stores; we write 4-byte
fields with `struct.pack_into`, which is a single aligned store on x86).

| Offset | Type | Field | Written by |
| --- | --- | --- | --- |
| 0 | u32 | DataID | tracker: +1 per pose; 0 when a new game ID appears; client resets it above 2^29 |
| 4 | i32 | CamWidth | 100 |
| 8 | i32 | CamHeight | 250 |
| 12 | 6 × f32 | Yaw, Pitch, Roll, X, Y, Z | tracker: **−yaw rad, −pitch rad, +roll rad, x·10, y·10, z·10** (cm → mm) |
| 36 | 6 × f32 | RawYaw … RawZ | tracker: −yaw, **+pitch**, +roll (rad), ·10 |
| 60 | 8 × f32 | X1..Y4 | unused (0) |
| 92 | i32 | GameID | **the game's DLL** writes its international ID here |
| 96 | 8 × u8 | table | tracker: the game's key from the game table |
| 104 | i32 | GameID2 | tracker: echo of GameID |

opentrack nudges a pitch of exactly +90° to 89.86° (Falcon BMS); kept.

## 2. Registry

`HKCU\Software\Freetrack\FreetrackClient\Path` and
`HKCU\Software\NaturalPoint\NATURALPOINT\NPClient Location\Path` = directory of the DLLs with
forward slashes and a trailing slash (`C:/…/headtrack_pc/libs/`). An interface that is switched
off gets an empty string, like opentrack. The values are left in place on exit (opentrack does the
same unless "ephemeral" is ticked), so a game started later still finds the DLLs; they only matter
while something writes the mapping.

## 3. Game table

`data/games.csv` (opentrack's `settings/facetracknoir supported games.csv`, 745 games). When the
game's DLL writes its ID, the 8-byte `table` is filled from the FaceTrackNoIR ID column for entries
newer than `V160` (first 8 of 11 hex bytes), else zeros; `GameID2` echoes the ID; `DataID` restarts at 0.
BeamNG.drive is ID 4525 (V160 → zero table).

## 4. Dummy `TrackIR.exe`

Some TrackIR games check for a running `TrackIR.exe`. opentrack ships a tiny one and starts it when
the TrackIR interface is enabled; we start the same file from `headtrack_pc/libs/` and kill it on exit.

## What is verified

- Struct size and every offset (`tests/test_outputs.py`), value conversion and the 90° nudge,
  DataID / GameID handshake sequence, game table lookup rules, registry value format: **tested on Linux**.
- Opening the Windows mapping/mutex, the registry writes, the dummy process, and a game actually
  reading the pose: **not yet run** (needs Windows). This is the first thing to check on the PC;
  `docs/windows-setup.md` says how.
