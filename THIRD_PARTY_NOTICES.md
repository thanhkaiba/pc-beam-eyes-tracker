# Third-party notices

## opentrack (ISC licence)

`headtrack_pc/outputs/freetrack.py` re-implements the behaviour of opentrack's `proto-ft`
module (shared-memory layout from `freetrackclient/fttypes.h`, registry keys, game table lookup
from `csv/csv.cpp`). `data/games.csv` is opentrack's `settings/facetracknoir supported games.csv`.
`tools/fetch_opentrack_libs.py` downloads `NPClient.dll`, `NPClient64.dll`, `freetrackclient.dll`,
`freetrackclient64.dll` and `TrackIR.exe` from the opentrack repository's `bin/` directory (tag
opentrack-2026.1.0, SHA-256 pinned) into `headtrack_pc/libs/`; those binaries are redistributed
unchanged. `NPClient*.dll` is built from opentrack's `contrib/npclient/npclient.c`, written by
uglyDwarf of the linuxtrack project (MIT licence) as credited there.

Copyright (c) 2012-2026 Stanislaw Halik and opentrack contributors; freetrackclient types loosely
translated from the FreeTrack project's Delphi sources by Wim Vriend and Ron Hendriks.

Permission to use, copy, modify, and/or distribute this software for any purpose with or without
fee is hereby granted, provided that the above copyright notice and this permission notice appear
in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS
SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE
OF THIS SOFTWARE.

Full text per module: <https://github.com/opentrack/opentrack/blob/master/OPENTRACK-LICENSING.txt>.

## MediaPipe Face Landmarker model (Apache License 2.0)

`face_landmarker.task` (downloaded by `tools/fetch_models.py`) is part of MediaPipe,
Copyright Google LLC, Apache License 2.0: <https://www.apache.org/licenses/LICENSE-2.0>.

## Road panorama (CC0)

`road_pano.jpg` (downloaded and cut by `tools/fetch_assets.py`) is "Goegap Road" by Greg Zaal,
Poly Haven, CC0 1.0 (public domain): <https://polyhaven.com/a/goegap_road>.

## Lucide icons (ISC)

`lucide.ttf` (downloaded by `tools/fetch_assets.py`, licence saved next to it as `LICENSE-lucide.txt`):
Lucide Contributors, portions Cole Bemis (Feather, MIT). ISC License: <https://lucide.dev/license>.

## Python packages

mediapipe (Apache 2.0), opencv-python (Apache 2.0), numpy (BSD-3), Pillow (MIT-CMU), sv-ttk (MIT), py7zr (LGPL-2.1, build tool
only), PyInstaller (GPL with bootloader exception, build tool only).
