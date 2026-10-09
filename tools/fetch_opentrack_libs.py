#!/usr/bin/env python3
"""Downloads the opentrack portable release and extracts the files games need:

  NPClient.dll, NPClient64.dll        TrackIR API shim (ISC licence, opentrack)
  freetrackclient.dll, freetrackclient64.dll   FreeTrack API shim (opentrack)
  TrackIR.exe                          dummy process some TrackIR games check for

into headtrack_pc/libs/. opentrack is not installed or run; only these files are kept.
Requires: pip install py7zr
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import urllib.request

VERSION = "2026.1.0"
URL = f"https://github.com/opentrack/opentrack/releases/download/opentrack-{VERSION}/opentrack-{VERSION}-win32-portable.7z"
WANTED = ("NPClient.dll", "NPClient64.dll", "freetrackclient.dll", "freetrackclient64.dll", "TrackIR.exe")
DEST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "headtrack_pc", "libs")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    archive = argv[0] if argv else None
    try:
        import py7zr
    except ImportError:
        print("pip install py7zr first", file=sys.stderr)
        return 2
    os.makedirs(DEST_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        if archive is None:
            archive = os.path.join(tmp, "opentrack.7z")
            print(f"downloading {URL} (about 190 MB)")
            with urllib.request.urlopen(URL, timeout=120) as r, open(archive, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
        with py7zr.SevenZipFile(archive, "r") as z:
            names = z.getnames()
            picks = {}
            for n in names:
                base = os.path.basename(n)
                if base in WANTED and base not in picks:
                    picks[base] = n
            missing = [w for w in WANTED if w not in picks]
            if missing:
                print(f"not found in archive: {missing}", file=sys.stderr)
            z.extract(path=tmp, targets=list(picks.values()))
            for base, n in picks.items():
                shutil.copy2(os.path.join(tmp, n), os.path.join(DEST_DIR, base))
                print(f"saved {os.path.join(DEST_DIR, base)}")
    notice = os.path.join(DEST_DIR, "NOTICE.txt")
    with open(notice, "w", encoding="utf-8") as f:
        f.write(f"Files in this directory are from opentrack {VERSION} (https://github.com/opentrack/opentrack),\n"
                "copyright Stanislaw Halik and contributors, ISC licence (see THIRD_PARTY_NOTICES.md).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
