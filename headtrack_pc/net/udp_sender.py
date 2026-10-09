"""Unicast UDP sender to an opentrack ("UDP over network") or another HeadTrack PC."""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SendStats:
    packets_sent: int = 0
    bytes_sent: int = 0
    send_errors: int = 0
    last_send_nanos: int = 0
    last_error: Optional[str] = None
    last_refused_nanos: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def reset(self) -> None:
        with self.lock:
            self.packets_sent = self.bytes_sent = self.send_errors = 0
            self.last_send_nanos = self.last_refused_nanos = 0
            self.last_error = None


class UdpSender:
    """Connected UDP socket on an ephemeral port. ICMP "port unreachable" is recorded and the packet retried once."""

    REFUSED_MESSAGE = "The PC answered that nothing is listening on that port"

    def __init__(self, host: str, port: int, stats: Optional[SendStats] = None, clock=time.monotonic_ns):
        self.stats = stats or SendStats()
        self._clock = clock
        self.target = (socket.gethostbyname(host), int(port))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.connect(self.target)
        self._lock = threading.Lock()

    @property
    def local_port(self) -> int:
        return self._sock.getsockname()[1]

    def send(self, data: bytes) -> bool:
        return self._send(data, self.stats)

    def send_uncounted(self, data: bytes) -> bool:
        return self._send(data, None)

    def _send(self, data: bytes, stats: Optional[SendStats]) -> bool:
        with self._lock:
            try:
                try:
                    self._sock.send(data)
                except ConnectionRefusedError:
                    with self.stats.lock:
                        self.stats.last_refused_nanos = self._clock()
                        self.stats.last_error = self.REFUSED_MESSAGE
                    self._sock.send(data)
                if stats is not None:
                    with stats.lock:
                        stats.packets_sent += 1
                        stats.bytes_sent += len(data)
                        stats.last_send_nanos = self._clock()
                return True
            except OSError as e:
                with self.stats.lock:
                    self.stats.send_errors += 1
                    self.stats.last_error = str(e)
                return False

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
