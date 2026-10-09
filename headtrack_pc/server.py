"""Local HTTP API and streaming overlay (stdlib only, 127.0.0.1 by default).

  GET /state.json   head pose, gaze point, link and game status as JSON (polled by tools, scripts, mods)
  GET /overlay.html transparent page that draws the gaze bubble: add it to OBS as a Browser Source
  GET /             short docs

No authentication: the server binds to localhost only unless the user changes the bind address.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second listener take the same port silently (no conflict
    # error), so leave it off there; on POSIX it only shortens TIME_WAIT after a restart.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True
from typing import Callable, Dict, Optional

DEFAULT_PORT = 4245

OVERLAY_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>HeadTrack gaze overlay</title>
<style>html,body{margin:0;background:transparent;overflow:hidden;width:100vw;height:100vh}
#b{position:absolute;width:%(size)dpx;height:%(size)dpx;border-radius:50%%;border:3px solid rgba(255,255,255,.9);
background:rgba(80,180,255,.35);box-shadow:0 0 18px rgba(80,180,255,.8);transform:translate(-50%%,-50%%);
transition:left .06s linear,top .06s linear;display:none}</style></head><body><div id="b"></div>
<script>
const b=document.getElementById('b');
async function tick(){try{const r=await fetch('/state.json',{cache:'no-store'});const s=await r.json();
if(s.gaze&&s.gaze.on_screen){b.style.display='block';b.style.left=(s.gaze.x*100)+'vw';b.style.top=(s.gaze.y*100)+'vh';}
else{b.style.display='none';}}catch(e){b.style.display='none';}setTimeout(tick,%(interval)d);}tick();
</script></body></html>"""

INDEX_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>HeadTrack PC API</title></head><body style="font-family:sans-serif;max-width:720px;margin:2em auto">
<h2>HeadTrack PC local API</h2>
<p><a href="/state.json">/state.json</a>: head pose (degrees, cm), gaze point (0..1 of the screen), tracking state, game, phone link. Poll it from scripts, Unity, Python, a Twitch bot.</p>
<p><a href="/overlay.html">/overlay.html</a>: gaze bubble overlay. In OBS add a <b>Browser Source</b> with this URL, width and height of your screen, and tick "Shutdown source when not visible" off.</p>
<p>Parameters for the overlay: <code>?size=48&amp;interval=50</code> (bubble diameter in px, poll interval in ms).</p>
<p>Everything is served on this PC only (127.0.0.1); nothing leaves it.</p></body></html>"""


class StateServer:
    def __init__(self, state_fn: Callable[[], Dict], port: int = DEFAULT_PORT, bind: str = "127.0.0.1"):
        self._state_fn = state_fn
        self.port = port
        self.bind = bind
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.error: Optional[str] = None
        self.requests = 0

    @property
    def bound_port(self) -> int:
        return self._server.server_address[1] if self._server else 0

    @property
    def url(self) -> str:
        return f"http://{self.bind}:{self.bound_port or self.port}/"

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(self, code: int, body: bytes, ctype: str):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.requests += 1
                path, _, query = self.path.partition("?")
                params = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
                if path == "/state.json":
                    try:
                        body = json.dumps(outer._state_fn()).encode("utf-8")
                    except Exception as e:  # never kill the server on a state error
                        body = json.dumps({"error": str(e)}).encode("utf-8")
                    self._send(200, body, "application/json")
                elif path == "/overlay.html":
                    try:
                        size = max(8, min(400, int(params.get("size", "48"))))
                        interval = max(20, min(1000, int(params.get("interval", "50"))))
                    except ValueError:
                        size, interval = 48, 50
                    self._send(200, (OVERLAY_HTML % {"size": size, "interval": interval}).encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/":
                    self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
                else:
                    self._send(404, b"not found", "text/plain")

        try:
            self._server = _Server((self.bind, self.port), Handler)
        except OSError as e:
            self.error = f"Local API port {self.port} unavailable: {e}"
            return
        self._thread = threading.Thread(target=self._server.serve_forever, name="api", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
