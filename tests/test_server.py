import json
import unittest
import urllib.request

from headtrack_pc.server import StateServer


class ServerTest(unittest.TestCase):
    def test_endpoints(self):
        state = {"pose": {"yaw": 1.5}, "gaze": {"x": 0.25, "y": 0.75, "on_screen": True}}
        s = StateServer(lambda: state, port=0)
        s.start()
        self.assertIsNone(s.error)
        try:
            base = f"http://127.0.0.1:{s.bound_port}"
            with urllib.request.urlopen(base + "/state.json", timeout=3) as r:
                self.assertEqual(r.headers["Content-Type"], "application/json")
                self.assertEqual(json.loads(r.read())["gaze"]["x"], 0.25)
            with urllib.request.urlopen(base + "/overlay.html?size=64&interval=40", timeout=3) as r:
                html = r.read().decode()
                self.assertIn("width:64px", html)
                self.assertIn("setTimeout(tick,40)", html)
            with urllib.request.urlopen(base + "/overlay.html?size=abc", timeout=3) as r:
                self.assertIn("width:48px", r.read().decode())
            with urllib.request.urlopen(base + "/", timeout=3) as r:
                self.assertIn("Browser Source", r.read().decode())
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/nope", timeout=3)
            state_err = StateServer(lambda: (_ for _ in ()).throw(RuntimeError("boom")), port=0)
            state_err.start()
            with urllib.request.urlopen(f"http://127.0.0.1:{state_err.bound_port}/state.json", timeout=3) as r:
                self.assertIn("boom", json.loads(r.read())["error"])
            state_err.stop()
            self.assertGreaterEqual(s.requests, 5)
            self.assertTrue(s.url.startswith("http://127.0.0.1:"))
            busy = StateServer(lambda: {}, port=s.bound_port)
            busy.start()
            self.assertIsNotNone(busy.error)
        finally:
            s.stop()
