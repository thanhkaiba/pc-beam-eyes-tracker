"""Pose sources feed the engine from their own thread."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from ..pose import HeadPose


class SourceStatus(Enum):
    STOPPED = "Stopped"
    STARTING = "Starting"
    RUNNING = "Running"
    FAILED = "Failed"


@dataclass(frozen=True)
class Frame:
    """One tracker result. `pose` is None when no face was found."""
    pose: Optional[HeadPose]
    timestamp_nanos: int
    latency_ms: float = 0.0
    landmarks: int = 0


FrameCallback = Callable[[Frame], None]


class PoseSource:
    status: SourceStatus = SourceStatus.STOPPED
    error: Optional[str] = None

    def start(self, on_frame: FrameCallback) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError
