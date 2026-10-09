"""UDP output: the same 48-byte packet the phone sends, to an opentrack on this or another PC."""
from __future__ import annotations

from typing import Optional

from .. import protocol
from ..net.udp_sender import SendStats, UdpSender
from ..pose import HeadPose


class UdpOutput:
    def __init__(self, host: str, port: int, extended: bool = False, stats: Optional[SendStats] = None):
        self.sender = UdpSender(host, port, stats)
        self.extended = extended
        self.sequence = 0

    @property
    def stats(self) -> SendStats:
        return self.sender.stats

    def write(self, pose: HeadPose, raw: Optional[HeadPose] = None, flags: int = 0, timestamp_nanos: int = 0) -> bool:
        if self.extended:
            data = protocol.encode_extended(pose, self.sequence, timestamp_nanos, flags)
            self.sequence = (self.sequence + 1) & 0xFFFFFFFF
        else:
            data = protocol.encode_opentrack(pose)
        return self.sender.send(data)

    def close(self) -> None:
        self.sender.close()
