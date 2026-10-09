import math
import struct
import unittest

from headtrack_pc import protocol as P
from headtrack_pc.pose import HeadPose


class OpenTrackPacketTest(unittest.TestCase):
    def test_layout_and_order(self):
        data = P.encode_opentrack(HeadPose(yaw=4.0, pitch=5.0, roll=6.0, x=1.0, y=2.0, z=3.0))
        self.assertEqual(len(data), 48)
        self.assertEqual(struct.unpack("<6d", data), (1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
        self.assertEqual(P.decode_opentrack(data), HeadPose(4.0, 5.0, 6.0, 1.0, 2.0, 3.0))

    def test_clamp_and_reject(self):
        data = P.encode_opentrack(HeadPose(400.0, 0, 0, 900.0, 0, 0))
        self.assertEqual(struct.unpack("<6d", data)[3], 180.0)
        self.assertEqual(struct.unpack("<6d", data)[0], 500.0)
        with self.assertRaises(ValueError):
            P.encode_opentrack(HeadPose(float("nan"), 0, 0))
        self.assertIsNone(P.decode_opentrack(b"\0" * 47))
        self.assertIsNone(P.decode_opentrack(struct.pack("<6d", 0, 0, 0, float("inf"), 0, 0)))
        self.assertIsNone(P.decode_opentrack(struct.pack("<6d", 0, 0, 0, 181.0, 0, 0)))
        self.assertIsNone(P.decode_opentrack(struct.pack("<6d", 501.0, 0, 0, 0, 0, 0)))
        self.assertIsNone(P.decode_opentrack(b"\x7f" * 48))


class ExtendedPacketTest(unittest.TestCase):
    def test_round_trip(self):
        data = P.encode_extended(HeadPose(1, 2, 3), sequence=0xFFFFFFFF, timestamp_nanos=123456789, flags=P.FLAG_TRACKING_VALID | P.FLAG_CALIBRATED, confidence=0.5)
        self.assertEqual(len(data), 72)
        self.assertEqual(data[48:52], b"HTRK")
        d = P.decode_pose_packet(data)
        self.assertIsInstance(d, P.ExtendedPacket)
        self.assertEqual(d.sequence, 0xFFFFFFFF)
        self.assertEqual(d.timestamp_nanos, 123456789)
        self.assertTrue(d.tracking_valid and d.calibrated and not d.simulated)
        self.assertAlmostEqual(d.confidence, 0.5)

    def test_plain_and_invalid(self):
        self.assertIsInstance(P.decode_pose_packet(P.encode_opentrack(HeadPose(1, 2, 3))), HeadPose)
        self.assertIsInstance(P.decode_pose_packet(b"\0" * 10), P.Invalid)
        self.assertIsInstance(P.decode_pose_packet(b"\0" * 60), P.Invalid)
        bad = bytearray(P.encode_extended(HeadPose(1, 2, 3), 1, 1, 0))
        bad[48] = ord("X")
        self.assertIsInstance(P.decode_pose_packet(bytes(bad)), P.Invalid)
        bad = bytearray(P.encode_extended(HeadPose(1, 2, 3), 1, 1, 0))
        bad[52] = 2
        self.assertIn("version", P.decode_pose_packet(bytes(bad)).reason)
        self.assertIn("confidence", P.decode_pose_packet(P.encode_extended(HeadPose(1, 2, 3), 1, 1, 0, confidence=2.0)).reason)
        self.assertTrue(math.isnan(P.decode_pose_packet(P.encode_extended(HeadPose(1, 2, 3), 1, 1, 0)).confidence))
        # oversize datagram: the trailer is parsed from the fixed offset, extra bytes ignored (as the Kotlin decoder does)
        self.assertIsInstance(P.decode_pose_packet(P.encode_extended(HeadPose(1, 2, 3), 1, 1, 0) + b"\0"), P.ExtendedPacket)

    def test_sequence_delta(self):
        self.assertEqual(P.sequence_delta(0xFFFFFFFF, 0), 1)
        self.assertEqual(P.sequence_delta(5, 3), -2)
        self.assertEqual(P.sequence_delta(1, 11), 10)


class PingTest(unittest.TestCase):
    def test_round_trip_and_reject(self):
        data = P.encode_ping(P.PING_REQUEST, 7, 99)
        self.assertEqual(len(data), 20)
        self.assertTrue(P.is_ping(data))
        p = P.decode_ping(data)
        self.assertEqual((p.kind, p.sequence, p.timestamp_nanos), (0, 7, 99))
        self.assertIsNone(P.decode_ping(data + b"\0"))
        self.assertIsNone(P.decode_ping(b"HTPG" + b"\x02" + data[5:]))
        self.assertIsNone(P.decode_ping(b"HTPG\x01\x05" + data[6:]))
        self.assertFalse(P.is_ping(P.encode_opentrack(HeadPose(0, 0, 0))))


class DiscoveryTest(unittest.TestCase):
    def test_request(self):
        req = P.encode_discovery_request(0xDEADBEEF)
        self.assertEqual(len(req), 16)
        self.assertEqual(req[:4], b"HTDQ")
        self.assertTrue(P.is_discovery_request(req))
        self.assertEqual(P.decode_discovery_request(req), 0xDEADBEEF)
        self.assertIsNone(P.decode_discovery_request(req + b"\0"))
        self.assertIsNone(P.decode_discovery_request(b"HTDR" + req[4:]))
        self.assertIsNone(P.decode_discovery_request(req[:4] + b"\x02" + req[5:]))
        self.assertIsNone(P.decode_discovery_request(req[:5] + b"\x01" + req[6:]))

    def test_reply_round_trip(self):
        rep = P.encode_discovery_reply(4242, 42, P.CAP_ACCEPTS_POSE | P.CAP_ANSWERS_PINGS, "Gaming PC ☺")
        d = P.decode_discovery_reply(rep)
        self.assertEqual(d.track_port, 4242)
        self.assertEqual(d.nonce, 42)
        self.assertEqual(d.capabilities, 5)
        self.assertEqual(d.name, "Gaming PC ☺")
        self.assertEqual(rep[:4], b"HTDR")
        self.assertEqual(rep[5], 1)
        self.assertEqual(len(rep), 16 + len("Gaming PC ☺".encode("utf-8")))

    def test_reply_name_truncation_keeps_code_points(self):
        rep = P.encode_discovery_reply(4242, 1, 0, "é" * 40)
        d = P.decode_discovery_reply(rep)
        self.assertEqual(d.name, "é" * 32)
        self.assertEqual(len(rep), 16 + 64)
        self.assertEqual(P.truncate_utf8("abc", 2), b"ab")

    def test_reply_rejections(self):
        rep = P.encode_discovery_reply(4242, 1, 0, "PC")
        self.assertIsNone(P.decode_discovery_reply(rep[:15]))
        self.assertIsNone(P.decode_discovery_reply(rep + b"x"))
        self.assertIsNone(P.decode_discovery_reply(b"HTDQ" + rep[4:]))
        self.assertIsNone(P.decode_discovery_reply(rep[:4] + b"\x02" + rep[5:]))
        self.assertIsNone(P.decode_discovery_reply(rep[:5] + b"\x00" + rep[6:]))
        bad_port = bytearray(rep)
        struct.pack_into("<H", bad_port, 6, 0)
        self.assertIsNone(P.decode_discovery_reply(bytes(bad_port)))
        too_long = bytearray(rep)
        too_long[14] = 65
        self.assertIsNone(P.decode_discovery_reply(bytes(too_long) + b"\0" * 63))
        invalid_utf8 = bytearray(P.encode_discovery_reply(4242, 1, 0, "ab"))
        invalid_utf8[16] = 0xFF
        d = P.decode_discovery_reply(bytes(invalid_utf8))
        self.assertIsNotNone(d)
        self.assertIsNone(d.name)
        with self.assertRaises(ValueError):
            P.encode_discovery_reply(0, 1, 0, "x")
