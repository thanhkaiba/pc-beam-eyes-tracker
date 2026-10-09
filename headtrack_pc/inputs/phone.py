"""The Android app as a pose source. Its packets are already calibrated, mapped and filtered on
the phone, so the engine passes them to the outputs unchanged (no second mapping)."""
from __future__ import annotations

import time
from typing import Callable, Optional, Tuple

from .. import protocol
from ..net.phone_receiver import PhoneReceiver, ReceiveStats
from ..pose import HeadPose
from .base import Frame, FrameCallback, PoseSource, SourceStatus


class PhoneSource(PoseSource):
    def __init__(self, port: int, discovery_reply: Callable[[int], Optional[bytes]], bind: str = "0.0.0.0",
                 clock=time.monotonic_ns):
        self.port = port
        self._bind = bind
        self._discovery_reply = discovery_reply
        self._clock = clock
        self._receiver: Optional[PhoneReceiver] = None
        self._on_frame: Optional[FrameCallback] = None
        self.last_extended: Optional[protocol.ExtendedPacket] = None
        self.status = SourceStatus.STOPPED
        self.error = None

    def start(self, on_frame: FrameCallback) -> None:
        self._on_frame = on_frame
        try:
            self._receiver = PhoneReceiver(self.port, self._on_pose, self._discovery_reply, self._bind, self._clock)
        except OSError as e:
            self.error = f"Cannot listen on UDP port {self.port}: {e}. Is opentrack or another HeadTrack running?"
            self.status = SourceStatus.FAILED
            return
        self._receiver.start()
        self.status = SourceStatus.RUNNING
        self.error = None

    def _on_pose(self, pose: HeadPose, ext: Optional[protocol.ExtendedPacket], addr: Tuple[str, int], now: int) -> None:
        self.last_extended = ext
        cb = self._on_frame
        if cb is not None:
            cb(Frame(pose, now))

    @property
    def stats(self) -> Optional[ReceiveStats]:
        return self._receiver.stats if self._receiver else None

    @property
    def bound_port(self) -> Optional[int]:
        return self._receiver.bound_port if self._receiver else None

    def stop(self) -> None:
        if self._receiver is not None:
            self._receiver.close()
            self._receiver = None
        self.status = SourceStatus.STOPPED
