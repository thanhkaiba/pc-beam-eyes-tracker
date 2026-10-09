"""The freetrack/TrackIR game table (opentrack's `facetracknoir supported games.csv`).

Each game identifies itself to the client DLL with an "international ID"; a few games also
expect an 8-byte key ("table") derived from the FaceTrackNoIR ID column. The lookup mirrors
opentrack's csv/csv.cpp: V160 entries and malformed IDs get an all-zero table.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

import sys


def _data_file() -> str:
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = [os.path.join(here, "data", "games.csv")]
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(getattr(sys, "_MEIPASS", ""), "data", "games.csv"))
        candidates.append(os.path.join(os.path.dirname(sys.executable), "data", "games.csv"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0]


DATA_FILE = _data_file()


@dataclass(frozen=True)
class GameEntry:
    game_id: int
    name: str
    table: bytes  # 8 bytes


def parse_games(text: str) -> Dict[int, GameEntry]:
    games: Dict[int, GameEntry] = {}
    for line in text.splitlines():
        line = line.strip("\r\n")
        if not line:
            continue
        cols = line.split(";")
        if len(cols) != 8:
            continue
        try:
            game_id = int(cols[6].strip())
        except ValueError:
            continue  # header or junk
        table = bytes(8)
        since = cols[3].strip()
        ftn = cols[7].strip()
        if since != "V160" and len(ftn) == 22:
            try:
                raw = bytes.fromhex(ftn)
                table = raw[:8]
            except ValueError:
                pass
        if game_id not in games:
            games[game_id] = GameEntry(game_id, cols[1].strip(), table)
    return games


_cache: Optional[Dict[int, GameEntry]] = None


def load_games(path: str = DATA_FILE) -> Dict[int, GameEntry]:
    global _cache
    if _cache is None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                _cache = parse_games(f.read())
        except OSError:
            _cache = {}
    return _cache


def lookup(game_id: int, games: Optional[Dict[int, GameEntry]] = None) -> GameEntry:
    games = games if games is not None else load_games()
    return games.get(game_id) or GameEntry(game_id, "Unknown game", bytes(8))
