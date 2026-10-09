#!/usr/bin/env python3
"""Downloads the client files games load, from the opentrack repository (tag opentrack-2026.1.0):

  NPClient.dll, NPClient64.dll             TrackIR API shim (opentrack contrib/npclient, by uglyDwarf)
  freetrackclient.dll, freetrackclient64.dll   FreeTrack API shim (opentrack freetrackclient/)
  TrackIR.exe                              dummy process some TrackIR games check for

into headtrack_pc/libs/. opentrack keeps these prebuilt binaries in its `bin/` directory, so no
190 MB release archive is needed. Every file is checked against a pinned SHA-256.
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request

TAG = "opentrack-2026.1.0"
BASE = f"https://raw.githubusercontent.com/opentrack/opentrack/{TAG}/bin/"
FILES = {
    "NPClient.dll": "53335e3036edaea2d448e46f1da322e64c036947012f353f7bca2b608272067c",
    "NPClient64.dll": "4304e6ad75da96295d55447463527eebf9c3eca1624c38263e4e3fa48df7ae6f",
    "freetrackclient.dll": "180dde82dc19a58326faf3ce44134d40d1abbed2129aab610cec51344a70c717",
    "freetrackclient64.dll": "a39deabb2449ec18c55251a9c15f61118b61ca53b2a9b0f473e047e55c965eeb",
    "TrackIR.exe": "17f06a7af0ab914d66c213526977ea72160f18ee37dec04bc0d2adcd58ebeb96",
}
DEST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "headtrack_pc", "libs")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    os.makedirs(DEST_DIR, exist_ok=True)
    bad = 0
    for name, digest in FILES.items():
        dest = os.path.join(DEST_DIR, name)
        if os.path.isfile(dest) and sha256(dest) == digest:
            print(f"ok       {dest}")
            continue
        url = BASE + name
        print(f"download {url}")
        tmp = dest + ".part"
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                f.write(r.read())
        except OSError as e:
            print(f"FAILED   {name}: {e}", file=sys.stderr)
            bad += 1
            continue
        got = sha256(tmp)
        if got != digest:
            os.remove(tmp)
            print(f"FAILED   {name}: sha256 {got} != {digest}", file=sys.stderr)
            bad += 1
            continue
        os.replace(tmp, dest)
        print(f"saved    {dest}")
    with open(os.path.join(DEST_DIR, "NOTICE.txt"), "w", encoding="utf-8") as f:
        f.write(f"Files in this directory are from opentrack {TAG} bin/ (https://github.com/opentrack/opentrack).\n"
                "freetrackclient*.dll and TrackIR.exe: opentrack, ISC licence. NPClient*.dll: opentrack contrib/npclient\n"
                "by uglyDwarf (linuxtrack). See THIRD_PARTY_NOTICES.md.\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
