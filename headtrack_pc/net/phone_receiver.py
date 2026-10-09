"""Receives the Android app's packets on the tracking port (default 4242, like opentrack),
answers its pings and discovery requests on that same socket, and keeps link statistics."""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

from .. import protocol
from ..pose import HeadPose

PoseCallback = Callable[[HeadPose, Optional[protocol.ExtendedPacket], Tuple[str, int], int], None]


@dataclass
class ReceiveStats:
    packets: int = 0
    extended: int = 0
    invalid: int = 0
    pings: int = 0
    discoveries: int = 0
    lost: int = 0
    last_packet_nanos: int = 0
    last_sender: Optional[Tuple[str, int]] = None
    last_sequence: Optional[int] = None
    last_invalid_reason: Optional[str] = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def snapshot(self) -> "ReceiveStats":
        with self.lock:
            return ReceiveStats(self.packets, self.extended, self.invalid, self.pings, self.discoveries, self.lost,
                                self.last_packet_nanos, self.last_sender, self.last_sequence, self.last_invalid_reason)


class PhoneReceiver:
    """One UDP socket bound to 0.0.0.0:port. Datagrams are told apart by size and magic:
    16-byte HTDQ → discovery reply, 20-byte HTPG → ping reply, 48/72 bytes → pose."""

    def __init__(self, port: int, on_pose: PoseCallback, discovery_reply: Callable[[int], Optional[bytes]],
                 bind: str = "0.0.0.0", clock=time.monotonic_ns):
        self.port = port
        self._on_pose = on_pose
        self._discovery_reply = discovery_reply
        self._clock = clock
        self.stats = ReceiveStats()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((bind, port))
        self._sock.settimeout(0.5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="phone-receiver", daemon=True)

    @property
    def bound_port(self) -> int:
        return self._sock.getsockname()[1]

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            self.handle(data, addr)

    def handle(self, data: bytes, addr: Tuple[str, int]) -> None:
        """Processes one datagram (public so tests can drive it without a socket)."""
        now = self._clock()
        if protocol.is_ping(data):
            ping = protocol.decode_ping(data)
            if ping is not None and ping.kind == protocol.PING_REQUEST:
                with self.stats.lock:
                    self.stats.pings += 1
                self._reply(protocol.encode_ping(protocol.PING_REPLY, ping.sequence, ping.timestamp_nanos), addr)
            return
        if protocol.is_discovery_request(data):
            nonce = protocol.decode_discovery_request(data)
            if nonce is not None:
                with self.stats.lock:
                    self.stats.discoveries += 1
                reply = self._discovery_reply(nonce)
                if reply:
                    self._reply(reply, addr)
            return
        decoded = protocol.decode_pose_packet(data)
        if isinstance(decoded, protocol.Invalid):
            with self.stats.lock:
                self.stats.invalid += 1
                self.stats.last_invalid_reason = decoded.reason
            return
        ext: Optional[protocol.ExtendedPacket] = None
        if isinstance(decoded, protocol.ExtendedPacket):
            ext = decoded
            pose = decoded.pose
        else:
            pose = decoded
        with self.stats.lock:
            self.stats.packets += 1
            self.stats.last_packet_nanos = now
            self.stats.last_sender = addr
            if ext is not None:
                self.stats.extended += 1
                if self.stats.last_sequence is not None:
                    d = protocol.sequence_delta(self.stats.last_sequence, ext.sequence)
                    if d > 1:
                        self.stats.lost += d - 1
                self.stats.last_sequence = ext.sequence
        self._on_pose(pose.with_time(now), ext, addr, now)

    def _reply(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            self._sock.sendto(data, addr)
        except OSError:
            pass

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.0)
