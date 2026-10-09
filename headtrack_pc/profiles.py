"""Presets and per-game profiles.

When a game's DLL reports its ID through the freetrack handshake, the app switches to that
game's tuning: the profile saved for it, or the preset for its category (driving, flight, other)
the first time. Edits made while a game is active are saved to that game's file, so each game
keeps its own sensitivity and curves without any manual profile juggling. Only the tuning
fields move with the game; camera, outputs and phone settings stay global.
"""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Dict, Optional

from . import profile as prof
from .autocentre import AutoCentreSettings
from .filters import FilterType, SmoothingSettings
from .gaze import EyeAssistSettings
from .mapping import AxisSettings, MappingSettings, ResponseCurve
from .profile import TrackingProfile

TUNING_FIELDS = ("mapping", "smoothing", "face_loss", "eye_assist", "auto_centre")

DRIVING = prof.DRIVING
FLIGHT = TrackingProfile(
    name="Flight",
    mapping=MappingSettings(
        # Flight sims expect to look back over the shoulder: 2.5× yaw up to 170°, 2× pitch, 1× roll, 6DOF on.
        yaw=AxisSettings(sensitivity=2.5, dead_zone=1.0, max_output=170.0, curve=ResponseCurve.SOFT),
        pitch=AxisSettings(sensitivity=2.0, dead_zone=1.0, max_output=80.0, curve=ResponseCurve.SOFT),
        roll=AxisSettings(sensitivity=1.0, dead_zone=1.5, max_output=60.0, curve=ResponseCurve.LINEAR),
        x=AxisSettings(enabled=True, sensitivity=1.5, dead_zone=0.5, max_output=30.0),
        y=AxisSettings(enabled=True, sensitivity=1.5, dead_zone=0.5, max_output=30.0),
        z=AxisSettings(enabled=True, sensitivity=1.5, dead_zone=0.5, max_output=30.0),
    ),
    smoothing=SmoothingSettings(type=FilterType.ONE_EURO, strength=0.45, one_euro_beta=0.05),
)
PASSTHROUGH = prof.PASSTHROUGH
PRESETS: Dict[str, TrackingProfile] = {"driving": DRIVING, "flight": FLIGHT, "passthrough": PASSTHROUGH}

# International IDs from data/games.csv → preset. Anything else gets "driving" (the safer default
# on a single screen); the user can switch the preset per game.
DRIVING_GAMES = {4525, 13602, 13603, 8140, 14101, 7101, 11601, 7401, 7402, 7403, 3401, 8104, 8107, 8108, 8113,
                 8109, 8110, 8111, 8112, 8114, 2826}
FLIGHT_GAMES = {1006, 1001, 1008, 1901, 1902, 8901, 7701, 10101, 11301, 2901, 3001, 2302, 2303, 2305, 2307, 20440,
                8150, 8151, 8145, 13801, 1850, 1675, 2304, 3450, 3475, 8175, 20725}


def category_of(game_id: int) -> str:
    if game_id in FLIGHT_GAMES:
        return "flight"
    return "driving"


def with_tuning(base: TrackingProfile, source: TrackingProfile, name: Optional[str] = None) -> TrackingProfile:
    """`base` with the tuning fields taken from `source` (outputs, camera, phone untouched)."""
    fields = {f: getattr(source, f) for f in TUNING_FIELDS}
    return replace(base, name=name if name is not None else source.name, **fields)


class ProfileLibrary:
    """Per-game tuning files under <config dir>/games/<id>.json."""

    def __init__(self, directory: Optional[str] = None):
        self.directory = directory or os.path.join(prof.config_dir(), "games")

    def path_for(self, game_id: int) -> str:
        return os.path.join(self.directory, f"{int(game_id)}.json")

    def has_saved(self, game_id: int) -> bool:
        return os.path.isfile(self.path_for(game_id))

    def for_game(self, game_id: int, game_name: str, base: TrackingProfile) -> TrackingProfile:
        """Saved tuning for the game, else the category preset, laid over `base`'s global settings."""
        saved = prof.load(self.path_for(game_id)) if self.has_saved(game_id) else None
        if saved is not None and saved is not prof.DEFAULT:
            return with_tuning(base, saved, name=saved.name or game_name)
        preset = PRESETS[category_of(game_id)]
        return with_tuning(base, preset, name=f"{game_name} ({preset.name})")

    def save_for_game(self, game_id: int, p: TrackingProfile) -> None:
        prof.save(replace(p, neutral_pose=None), self.path_for(game_id))

    def forget(self, game_id: int) -> None:
        try:
            os.remove(self.path_for(game_id))
        except OSError:
            pass
