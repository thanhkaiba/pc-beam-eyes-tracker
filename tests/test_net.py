import socket
import threading
import time
import unittest

from headtrack_pc import protocol as P
from headtrack_pc.net.discovery import DiscoveryResponder, default_pc_name, local_ipv4_addresses
from headtrack_pc.net.phone_receiver import PhoneReceiver
from headtrack_pc.net.udp_sender import UdpSender
from headtrack_pc.outputs.udp import UdpOutput
from headtrack_pc.pose import HeadPose


class FakeClock:
    def __init__(self):
        self.now = 1_000_000_000

    def __call__(self):
        return self.now


class PhoneReceiverTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.poses = []
        self.receiver = PhoneReceiver(0, lambda pose, ext, addr, now: self.poses.append((pose, ext, addr)),
                                      lambda nonce: P.encode_discovery_reply(4242, nonce, 7, "Test PC"),
                                      bind="127.0.0.1", clock=self.clock)
        self.receiver.start()
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client.settimeout(2.0)
        self.target = ("127.0.0.1", self.receiver.bound_port)

    def tearDown(self):
        self.receiver.close()
        self.client.close()

    def wait(self, cond, timeout=2.0):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.01)
        return False

    def test_pose_plain_and_extended_with_loss(self):
        self.client.sendto(P.encode_opentrack(HeadPose(1, 2, 3)), self.target)
        self.assertTrue(self.wait(lambda: len(self.poses) == 1))
        self.assertEqual(self.poses[0][0].yaw, 1.0)
        self.assertIsNone(self.poses[0][1])
        self.assertEqual(self.poses[0][0].timestamp_nanos, self.clock.now)
        for seq in (10, 11, 14):
            self.client.sendto(P.encode_extended(HeadPose(seq, 0, 0), seq, 5, P.FLAG_TRACKING_VALID), self.target)
        self.assertTrue(self.wait(lambda: len(self.poses) == 4))
        s = self.receiver.stats.snapshot()
        self.assertEqual((s.packets, s.extended, s.lost, s.invalid), (4, 3, 2, 0))
        self.assertEqual(s.last_sequence, 14)
        self.assertEqual(s.last_sender[0], "127.0.0.1")

    def test_ping_is_answered_and_not_counted_as_pose(self):
        self.client.sendto(P.encode_ping(P.PING_REQUEST, 9, 777), self.target)
        data, _ = self.client.recvfrom(64)
        reply = P.decode_ping(data)
        self.assertEqual((reply.kind, reply.sequence, reply.timestamp_nanos), (P.PING_REPLY, 9, 777))
        self.assertEqual(self.receiver.stats.snapshot().packets, 0)
        self.assertEqual(self.receiver.stats.snapshot().pings, 1)

    def test_discovery_on_track_port(self):
        self.client.sendto(P.encode_discovery_request(123), self.target)
        data, _ = self.client.recvfrom(256)
        d = P.decode_discovery_reply(data)
        self.assertEqual((d.nonce, d.track_port, d.name, d.capabilities), (123, 4242, "Test PC", 7))

    def test_invalid_counted_and_dropped(self):
        self.client.sendto(b"\x7f" * 48, self.target)
        self.client.sendto(b"short", self.target)
        self.assertTrue(self.wait(lambda: self.receiver.stats.snapshot().invalid == 2))
        self.assertEqual(len(self.poses), 0)
        self.assertIn("too short", self.receiver.stats.snapshot().last_invalid_reason)

    def test_port_in_use_raises(self):
        with self.assertRaises(OSError):
            PhoneReceiver(self.receiver.bound_port, lambda *a: None, lambda n: None, bind="127.0.0.1")


class DiscoveryResponderTest(unittest.TestCase):
    def test_answers_with_live_capabilities(self):
        caps = {"v": P.CAP_ACCEPTS_POSE}
        r = DiscoveryResponder(0, 4242, lambda: "My PC", lambda: caps["v"], bind="127.0.0.1")
        r.start()
        try:
            c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            c.settimeout(2.0)
            c.sendto(P.encode_discovery_request(5), ("127.0.0.1", r.bound_port))
            d = P.decode_discovery_reply(c.recvfrom(256)[0])
            self.assertEqual((d.nonce, d.capabilities, d.name), (5, 1, "My PC"))
            caps["v"] |= P.CAP_GAME_OUTPUT
            c.sendto(P.encode_discovery_request(6), ("127.0.0.1", r.bound_port))
            self.assertEqual(P.decode_discovery_reply(c.recvfrom(256)[0]).capabilities, 9)
            c.sendto(b"junk", ("127.0.0.1", r.bound_port))
            c.sendto(P.encode_discovery_request(7), ("127.0.0.1", r.bound_port))
            self.assertEqual(P.decode_discovery_reply(c.recvfrom(256)[0]).nonce, 7)
            self.assertEqual(r.answered, 3)
            c.close()
        finally:
            r.close()

    def test_helpers(self):
        self.assertTrue(default_pc_name())
        self.assertIsInstance(local_ipv4_addresses(), list)


class UdpSenderTest(unittest.TestCase):
    def test_send_to_loopback_and_refused(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        server.settimeout(2.0)
        out = UdpOutput("127.0.0.1", server.getsockname()[1])
        self.assertTrue(out.write(HeadPose(1, 2, 3)))
        self.assertEqual(P.decode_opentrack(server.recvfrom(100)[0]).yaw, 1.0)
        out.extended = True
        out.write(HeadPose(4, 5, 6), flags=P.FLAG_CALIBRATED, timestamp_nanos=9)
        d = P.decode_pose_packet(server.recvfrom(100)[0])
        self.assertEqual((d.sequence, d.flags, d.timestamp_nanos), (0, P.FLAG_CALIBRATED, 9))
        self.assertEqual(out.stats.packets_sent, 2)
        port = server.getsockname()[1]
        server.close()
        out.close()
        # nothing listening: ICMP port unreachable surfaces on a later send (Linux) — must not raise
        s = UdpSender("127.0.0.1", port)
        for _ in range(3):
            s.send(b"x" * 48)
            time.sleep(0.02)
        self.assertGreaterEqual(s.stats.packets_sent, 1)
        s.close()
