import ctypes
import os
import tempfile
import unittest

from headtrack_pc import steam


class FakeFn:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if callable(self.result):
            return self.result(*args)
        return self.result


class FakeLib:
    """Looks like a ctypes.CDLL: attribute access returns callables with settable restype/argtypes."""

    def __init__(self, fns):
        self._fns = fns

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._fns:
            return self._fns[name]
        raise AttributeError(name)


def with_dll(fn):
    def wrapper(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "steam_api64.dll"), "wb").close()
            fn(self, d)
    return wrapper


class SteamTest(unittest.TestCase):
    def test_no_dll_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            s = steam.Steam(app_id=480, directory=d, loader=lambda p: self.fail("must not load"))
            st = s.start()
            self.assertFalse(st.available)
            self.assertFalse(st.initialized)
            self.assertIn("not found", st.message)
            s.run_callbacks()
            s.shutdown()

    @with_dll
    def test_no_app_id(self, d):
        s = steam.Steam(directory=d, loader=lambda p: self.fail("must not load"))
        self.assertFalse(s.start().available)
        self.assertIn("no app ID", s.status.message)

    @with_dll
    def test_app_id_from_file_and_env(self, d):
        with open(os.path.join(d, "steam_appid.txt"), "w") as f:
            f.write("480\n")
        self.assertEqual(steam.read_app_id(d), 480)
        os.environ["HEADTRACK_STEAM_APPID"] = "12345"
        try:
            self.assertEqual(steam.read_app_id(d), 12345)
        finally:
            del os.environ["HEADTRACK_STEAM_APPID"]

    @with_dll
    def test_restart_requested(self, d):
        lib = FakeLib({"SteamAPI_RestartAppIfNecessary": FakeFn(True)})
        s = steam.Steam(app_id=480, directory=d, loader=lambda p: lib)
        st = s.start()
        self.assertTrue(st.available and st.restart_requested and not st.initialized)
        self.assertEqual(lib.SteamAPI_RestartAppIfNecessary.calls, [(480,)])

    @with_dll
    def test_init_flat_success_with_name(self, d):
        def init_flat(err):
            return 0
        fns = {
            "SteamAPI_RestartAppIfNecessary": FakeFn(False),
            "SteamAPI_InitFlat": FakeFn(init_flat),
            "SteamAPI_SteamFriends_v017": FakeFn(0x1234),
            "SteamAPI_ISteamFriends_GetPersonaName": FakeFn(b"Thanh"),
            "SteamAPI_SteamUtils_v010": FakeFn(0x5678),
            "SteamAPI_ISteamUtils_GetAppID": FakeFn(480),
            "SteamAPI_RunCallbacks": FakeFn(None),
            "SteamAPI_Shutdown": FakeFn(None),
        }
        lib = FakeLib(fns)
        s = steam.Steam(app_id=1, directory=d, loader=lambda p: lib)
        st = s.start()
        self.assertTrue(st.initialized)
        self.assertEqual(st.persona_name, "Thanh")
        self.assertEqual(st.app_id, 480)
        self.assertIn("Thanh", st.message)
        s.run_callbacks()
        self.assertEqual(len(fns["SteamAPI_RunCallbacks"].calls), 1)
        s.shutdown()
        self.assertEqual(len(fns["SteamAPI_Shutdown"].calls), 1)
        s.run_callbacks()  # after shutdown: no more calls
        self.assertEqual(len(fns["SteamAPI_RunCallbacks"].calls), 1)

    @with_dll
    def test_init_flat_failure_message(self, d):
        def init_flat(err):
            ctypes.memmove(err, b"Steam is not running\0", 21)
            return 2
        lib = FakeLib({"SteamAPI_RestartAppIfNecessary": FakeFn(False), "SteamAPI_InitFlat": FakeFn(init_flat)})
        st = steam.Steam(app_id=1, directory=d, loader=lambda p: lib).start()
        self.assertFalse(st.initialized)
        self.assertIn("Steam is not running", st.message)

    @with_dll
    def test_old_sdk_init(self, d):
        lib = FakeLib({"SteamAPI_RestartAppIfNecessary": FakeFn(False), "SteamAPI_Init": FakeFn(True)})
        st = steam.Steam(app_id=1, directory=d, loader=lambda p: lib).start()
        self.assertTrue(st.initialized)
        self.assertEqual(st.persona_name, "")
        self.assertEqual(st.app_id, 1)

    @with_dll
    def test_dll_load_error(self, d):
        def loader(p):
            raise OSError("bad image")
        st = steam.Steam(app_id=1, directory=d, loader=loader).start()
        self.assertFalse(st.available)
        self.assertIn("bad image", st.message)
