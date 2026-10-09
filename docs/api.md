# Local API and streaming overlay

Served by `headtrack_pc/server.py` on `http://127.0.0.1:4245/` (Advanced → Streaming overlay and local API).

## `GET /state.json`

```json
{
  "version": 1,
  "tracking": "TRACKING", "paused": false, "source": "webcam",
  "face": true, "fps": 30.0, "centre_set": true,
  "raw":     {"yaw": 3.1, "pitch": -12.0, "roll": 0.4, "x": 0.2, "y": 1.1, "z": -48.0},
  "centred": {"yaw": 2.0, "pitch": 1.5,  "roll": 0.1, "x": 0.0, "y": 0.0, "z": 1.0},
  "sent":    {"yaw": 6.1, "pitch": 3.0,  "roll": 0.1, "x": 0.0, "y": 0.0, "z": 0.0},
  "eye_yaw": 4.0, "eye_pitch": 0.0,
  "gaze": {"x": 0.62, "y": 0.41, "on_screen": true, "quality": "good"},
  "game": {"id": 4525, "name": "Beamng.drive"}, "phone_connected": false
}
```

Angles in degrees (yaw right, pitch up, roll right), translation in cm; `sent` is what the game
receives (after centre, mapping, smoothing, extended view). `gaze` is null until the eye
tracking is calibrated; `x`/`y` are fractions of the primary screen (0,0 = top-left).

Poll it at 20–60 Hz; it is cheap. CORS is open so a browser page can read it. Localhost only
unless `api.bind` in the profile is changed.

## `GET /overlay.html?size=48&interval=50`

A transparent page with a gaze bubble. In OBS: Sources → + → Browser, URL from the app's
*Copy overlay URL*, width/height = your screen, refresh when the scene becomes active off.

## Packets

The UDP formats the phone and the UDP output speak are in `docs/discovery.md` and the Android
repo's `docs/protocol.md` (48-byte opentrack pose, 72-byte HeadTrack pose, 20-byte ping).
