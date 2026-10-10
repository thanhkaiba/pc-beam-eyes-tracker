"""Windows-only end-to-end check of the game output: our writer on one side, opentrack's real
client DLLs (what games load) on the other, in this process via ctypes.

Skipped elsewhere and when the DLLs have not been fetched (tools/fetch_opentrack_libs.py).
"""
import ctypes
import math
import os
import struct
import sys
import time
import unittest

from headtrack_pc.outputs import freetrack as FT
from headtrack_pc.pose import HeadPose

ON_WINDOWS = sys.platform == "win32"
LIBS = FT.find_libs_dir() if ON_WINDOWS else None
BITS = "64" if ctypes.sizeof(ctypes.c_void_p) == 8 else ""
NP_AXIS_MAX = 16383.0


class TirData(ctypes.Structure):
    _fields_ = [("status", ctypes.c_short), ("frame", ctypes.c_short), ("cksum", ctypes.c_uint),
                ("roll", ctypes.c_float), ("pitch", ctypes.c_float), ("yaw", ctypes.c_float),
                ("tx", ctypes.c_float), ("ty", ctypes.c_float), ("tz", ctypes.c_float),
                ("padding", ctypes.c_float * 9)]


class FTData(ctypes.Structure):
    _fields_ = [("DataID", ctypes.c_uint), ("CamWidth", ctypes.c_int), ("CamHeight", ctypes.c_int),
                ("Yaw", ctypes.c_float), ("Pitch", ctypes.c_float), ("Roll", ctypes.c_float),
                ("X", ctypes.c_float), ("Y", ctypes.c_float), ("Z", ctypes.c_float),
                ("RawYaw", ctypes.c_float), ("RawPitch", ctypes.c_float), ("RawRoll", ctypes.c_float),
                ("RawX", ctypes.c_float), ("RawY", ctypes.c_float), ("RawZ", ctypes.c_float),
                ("points", ctypes.c_float * 8)]


@unittest.skipUnless(ON_WINDOWS, "Windows only")
@unittest.skipUnless(LIBS, "client DLLs not fetched")
class WindowsFreetrackTest(unittest.TestCase):
    def setUp(self):
        # FT_SharedMem is one name for the whole session: with HeadTrack PC or opentrack open, these tests
        # would register a fake game (BeamNG) in that app and fight it over the dummy TrackIR.exe
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenFileMappingW.restype = ctypes.c_void_p
        k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        existing = k32.OpenFileMappingW(0x0004, 0, FT.SHM_NAME)   # FILE_MAP_READ
        if existing:
            k32.CloseHandle(existing)
            self.skipTest("another tracker (HeadTrack PC, opentrack) holds FT_SharedMem: close it to run these tests")
        self.out = FT.FreetrackOutput("both", start_dummy=True)

    def tearDown(self):
        self.out.close()

    def test_struct_sizes(self):
        self.assertEqual(ctypes.sizeof(FTData), FT.FTDATA_SIZE)
        self.assertEqual(ctypes.sizeof(TirData), 68)

    def test_registry_points_at_libs(self):
        import winreg
        for key in (r"Software\Freetrack\FreetrackClient", r"Software\NaturalPoint\NATURALPOINT\NPClient Location"):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                value, kind = winreg.QueryValueEx(k, "Path")
            self.assertEqual(kind, winreg.REG_SZ)
            self.assertEqual(value, FT.registry_path_value(self.out.libs_dir))
            self.assertTrue(os.path.isfile(os.path.join(value, "NPClient.dll")))

    def test_freetrackclient_dll_reads_our_pose(self):
        dll = ctypes.WinDLL(os.path.join(LIBS, f"freetrackclient{BITS}.dll"))
        dll.FTGetData.restype = ctypes.c_int
        dll.FTGetData.argtypes = [ctypes.POINTER(FTData)]
        self.out.write(HeadPose(10.0, 20.0, 30.0, 1.0, 2.0, 3.0))
        data = FTData()
        self.assertTrue(dll.FTGetData(ctypes.byref(data)))
        self.assertAlmostEqual(data.Yaw, -math.radians(10.0), places=4)
        self.assertAlmostEqual(data.Pitch, -math.radians(20.0), places=4)
        self.assertAlmostEqual(data.Roll, math.radians(30.0), places=4)
        self.assertEqual((data.X, data.Y, data.Z), (10.0, 20.0, 30.0))
        self.assertEqual((data.CamWidth, data.CamHeight), (100, 250))
        first = data.DataID
        self.out.write(HeadPose(10.0, 20.0, 30.0, 1.0, 2.0, 3.0))
        dll.FTGetData(ctypes.byref(data))
        self.assertEqual(data.DataID, first + 1)

    def test_npclient_dll_handshake_and_data(self):
        dll = ctypes.WinDLL(os.path.join(LIBS, f"NPClient{BITS}.dll"))
        dll.NP_RegisterProgramProfileID.argtypes = [ctypes.c_ushort]
        dll.NP_StartDataTransmission.restype = ctypes.c_int
        dll.NP_GetData.argtypes = [ctypes.POINTER(TirData)]
        dll.NP_GetData.restype = ctypes.c_int
        self.assertEqual(dll.NP_RegisterProgramProfileID(4525), 0)  # BeamNG.drive
        self.assertEqual(dll.NP_StartDataTransmission(), 0)
        # the next pose write answers the game: table + GameID2, name resolved
        self.out.write(HeadPose(10.0, -20.0, 5.0))
        self.assertEqual(self.out.game_id, 4525)
        self.assertEqual(self.out.game_name, "Beamng.drive")
        data = TirData()
        self.assertEqual(dll.NP_GetData(ctypes.byref(data)), 0)  # 0 = running
        self.assertEqual(data.status, 0)
        # NPClient: yaw = Yaw_rad * 16383 / pi, with Yaw_rad = -yaw_deg in radians
        self.assertAlmostEqual(data.yaw, -10.0 * NP_AXIS_MAX / 180.0, delta=0.5)
        self.assertAlmostEqual(data.pitch, 20.0 * NP_AXIS_MAX / 180.0, delta=0.5)
        self.assertAlmostEqual(data.roll, 5.0 * NP_AXIS_MAX / 180.0, delta=0.5)
        self.out.write(HeadPose(-10.0, 0.0, 0.0))
        dll.NP_GetData(ctypes.byref(data))
        self.assertAlmostEqual(data.yaw, 10.0 * NP_AXIS_MAX / 180.0, delta=0.5)
        self.out.write(HeadPose(0.0, 0.0, 0.0))
        dll.NP_GetData(ctypes.byref(data))  # the shipped binary returns 0 here too; only the values matter
        self.assertEqual((data.yaw, data.pitch, data.roll), (0.0, 0.0, 0.0))

    def test_dummy_trackir_runs_and_is_killed(self):
        proc = self.out._dummy
        self.assertIsNotNone(proc, "TrackIR.exe should be started for the npclient interface")
        time.sleep(0.3)
        self.assertIsNone(proc.poll(), "dummy exited early")
        self.out.close()
        self.assertIsNotNone(proc.poll())
        self.out = FT.FreetrackOutput("freetrack", start_dummy=True)
        self.assertIsNone(self.out._dummy)  # not started for FreeTrack-only

    def test_second_writer_shares_the_mapping(self):
        # a second FreetrackOutput (another process in real life) sees the same bytes
        other = FT.FreetrackOutput("both", start_dummy=False)
        try:
            self.out.write(HeadPose(7.0, 0.0, 0.0))
            self.assertAlmostEqual(other.memory.read_pose()[0], -math.radians(7.0), places=4)
        finally:
            other.close()
