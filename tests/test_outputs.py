import math
import mmap
import struct
import sys
import unittest

from headtrack_pc.outputs import freetrack as FT
from headtrack_pc.outputs import games
from headtrack_pc.pose import HeadPose


class GamesTest(unittest.TestCase):
    def test_parse_rules(self):
        text = "No;Game Name;Game protocol;Supported since;Verified;By;INTERNATIONAL_ID;FTN_ID\n" \
               "1;Old Game;FreeTrack20;V160;V;x;100;00AABBCCDDEEFF0011223344\n" \
               "2;New Game;FreeTrack20;V170;;;200;0058F688FC9B0556868F00\n" \
               "3;Bad Hex;FreeTrack20;V170;;;300;ZZ58F688FC9B0556868F00\n" \
               "4;Short;FreeTrack20;V170;;;400;0058\n" \
               "junk line\n" \
               "5;Dup;FreeTrack20;V170;;;200;0000000000000000000000\n"
        g = games.parse_games(text)
        self.assertEqual(g[100].table, bytes(8))
        self.assertEqual(g[200].table, bytes.fromhex("0058F688FC9B0556"))
        self.assertEqual(g[200].name, "New Game")
        self.assertEqual(g[300].table, bytes(8))
        self.assertEqual(g[400].table, bytes(8))
        self.assertEqual(games.lookup(999, g).name, "Unknown game")

    def test_bundled_table(self):
        g = games.load_games()
        self.assertGreater(len(g), 700)
        self.assertEqual(games.lookup(4525).name, "Beamng.drive")
        self.assertEqual(games.lookup(1006).name, "DCS")
        self.assertEqual(games.lookup(1006).table.hex(), "0058f688fc9b0556")


class FreetrackMemoryTest(unittest.TestCase):
    def test_layout_matches_fttypes_h(self):
        # FTData: DataID(4) CamWidth(4) CamHeight(4) + 20 floats = 92; FTHeap adds GameID(4) table(8) GameID2(4)
        self.assertEqual(FT.FTDATA_SIZE, 92)
        self.assertEqual(FT.FTHEAP_SIZE, 108)
        self.assertEqual((FT.OFF_POSE, FT.OFF_RAW, FT.OFF_POINTS, FT.OFF_GAME_ID, FT.OFF_TABLE, FT.OFF_GAME_ID2), (12, 36, 60, 92, 96, 104))

    def test_values_follow_proto_ft(self):
        yaw, pitch, roll, x, y, z = FT.ft_values(HeadPose(10.0, 20.0, 30.0, 1.0, 2.0, 3.0))
        self.assertAlmostEqual(yaw, -math.radians(10.0))
        self.assertAlmostEqual(pitch, -math.radians(20.0))
        self.assertAlmostEqual(roll, math.radians(30.0))
        self.assertEqual((x, y, z), (10.0, 20.0, 30.0))
        self.assertAlmostEqual(FT.ft_values(HeadPose(0, 90.0, 0))[1], -math.radians(89.86))
        self.assertAlmostEqual(FT.ft_values(HeadPose(0, 89.0, 0))[1], -math.radians(89.0))
        r = FT.raw_values(HeadPose(10.0, 20.0, 30.0))
        self.assertAlmostEqual(r[1], math.radians(20.0))

    def test_write_sequence_and_game_handshake(self):
        buf = bytearray(FT.FTHEAP_SIZE)
        mem = FT.FreetrackMemory(buf, games.parse_games("1;DCS;FreeTrack20;V170;;;1006;0058F688FC9B0556868F00\n"))
        mem.initialize()
        self.assertEqual(struct.unpack_from("<Iii", buf, 0), (1, 100, 250))
        mem.write(HeadPose(10, 20, 30, 1, 2, 3))
        # first write: game id 0 ≠ -1 → handshake with "unknown", DataID reset to 0 (as opentrack does)
        self.assertEqual(struct.unpack_from("<I", buf, 0)[0], 0)
        self.assertEqual(mem.game_name, "Unknown game")
        mem.write(HeadPose(10, 20, 30, 1, 2, 3))
        mem.write(HeadPose(10, 20, 30, 1, 2, 3))
        self.assertEqual(struct.unpack_from("<I", buf, 0)[0], 2)
        self.assertAlmostEqual(mem.read_pose()[0], -math.radians(10.0), places=5)
        self.assertAlmostEqual(struct.unpack_from("<6f", buf, FT.OFF_RAW)[1], math.radians(20.0), places=5)
        struct.pack_into("<i", buf, FT.OFF_GAME_ID, 1006)  # the game's DLL reports itself
        mem.write(HeadPose(0, 0, 0))
        self.assertEqual(mem.game_name, "DCS")
        self.assertEqual(bytes(buf[FT.OFF_TABLE:FT.OFF_TABLE + 8]).hex(), "0058f688fc9b0556")
        self.assertEqual(struct.unpack_from("<i", buf, FT.OFF_GAME_ID2)[0], 1006)
        self.assertEqual(struct.unpack_from("<I", buf, 0)[0], 0)
        # the client resets DataID when it grows past 2^29; we continue from whatever it holds
        struct.pack_into("<I", buf, 0, 0)
        mem.write(HeadPose(0, 0, 0))
        self.assertEqual(struct.unpack_from("<I", buf, 0)[0], 1)

    def test_works_on_anonymous_mmap(self):
        m = mmap.mmap(-1, FT.FTHEAP_SIZE)
        mem = FT.FreetrackMemory(m)
        mem.initialize()
        mem.write(HeadPose(5, 0, 0))
        self.assertAlmostEqual(mem.read_pose()[0], -math.radians(5.0), places=5)
        m.close()
        with self.assertRaises(ValueError):
            FT.FreetrackMemory(bytearray(10))

    def test_registry_value_format(self):
        self.assertEqual(FT.registry_path_value(r"C:\Games\HeadTrack\libs"), "C:/Games/HeadTrack/libs/")
        self.assertEqual(FT.registry_path_value("C:/x/"), "C:/x/")

    def test_output_unavailable_off_windows(self):
        if sys.platform == "win32":
            self.skipTest("Windows")
        with self.assertRaises(FT.OutputUnavailable):
            FT.FreetrackOutput()
        self.assertNotEqual(FT.find_libs_dir(["/nonexistent"]), "/nonexistent")
