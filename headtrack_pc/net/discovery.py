"""Answers the phone's broadcast discovery requests (docs/discovery.md) on the discovery port."""
from __future__ import annotations

import socket
import threading
from typing import Callable, Optional, Tuple

from .. import protocol


class DiscoveryResponder:
    """Binds 0.0.0.0:port (default 4244). `capabilities` and `name` are callables so the reply
    always reflects the live engine state (game output active, webcam tracking, ...)."""

    def __init__(self, port: int, track_port: int, name: Callable[[], str], capabilities: Callable[[], int],
                 bind: str = "0.0.0.0"):
        self.port = port
        self.track_port = track_port
        self._name = name
        self._capabilities = capabilities
        self.answered = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((bind, port))
        self._sock.settimeout(0.5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="discovery", daemon=True)

    @property
    def bound_port(self) -> int:
        return self._sock.getsockname()[1]

    def start(self) -> None:
        self._thread.start()

    def reply_for(self, nonce: int) -> Optional[bytes]:
        return protocol.encode_discovery_reply(self.track_port, nonce, self._capabilities(), self._name())

    def handle(self, data: bytes, addr: Tuple[str, int]) -> Optional[bytes]:
        nonce = protocol.decode_discovery_request(data)
        if nonce is None:
            return None
        reply = self.reply_for(nonce)
        self.answered += 1
        return reply

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            reply = self.handle(data, addr)
            if reply:
                try:
                    self._sock.sendto(reply, addr)
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


def default_pc_name() -> str:
    try:
        return socket.gethostname() or "PC"
    except OSError:
        return "PC"


def local_ipv4_addresses() -> list:
    """Best-effort list of this PC's IPv4 addresses (for the on-screen "your PC is ..." hint)."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        if ip not in found and not ip.startswith("127."):
            found.insert(0, ip)
    except OSError:
        pass
    return found
