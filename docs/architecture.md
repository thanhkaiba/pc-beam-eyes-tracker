# Architecture

| Module | Responsibility | Android equivalent |
| --- | --- | --- |
| `pose.py` | `HeadPose`, wrap, matrix ↔ Euler (`from_transformation_matrix` takes MediaPipe Python's 4×4 or Android's flat column-major), `relative_to` | `core.pose` |
| `calibration.py` | sampling window → mean neutral with stability checks | `core.calibration.NeutralCalibrator` |
| `mapping.py` | dead zone → sensitivity → curve → clamp → invert, per axis | `core.mapping` |
| `filters.py` | Exponential / One Euro / pass-through, one strength slider | `core.filter` |
| `faceloss.py` | hold → ease to neutral → neutral | `core.pipeline.FaceLossHandler` |
| `pipeline.py` | raw → calibrated → mapped → filtered → output; calibrate / recenter / clear | `core.pipeline.TrackingPipeline` |
| `profile.py` | all settings + JSON codec (forward-compatible) + per-user file; PC-only: camera, outputs, phone ports | `core.config` |
| `protocol.py` | 48/72-byte pose, ping, discovery codecs | `core.protocol` (+ `DiscoveryPacket`) |
| `net/udp_sender.py` | connected UDP sender, refusal detection | `core.net.UdpSender` |
| `net/phone_receiver.py` | tracking-port socket: pose / ping / discovery by size+magic, loss stats | `tools/receiver` |
| `net/discovery.py` | discovery-port responder, PC name, local IPs | — (PC side of `PcDiscovery`) |
| `inputs/webcam.py` | OpenCV capture thread + MediaPipe LIVE_STREAM → `Frame` | `app.camera.FaceLandmarkerAnalyzer` |
| `inputs/phone.py` | `PhoneReceiver` as a source (frames are final poses) | — |
| `outputs/freetrack.py` | FT_SharedMem writer (portable) + Windows mapping/mutex/registry/dummy | opentrack `proto-ft` |
| `outputs/games.py` | game table | opentrack `csv/csv.cpp` |
| `outputs/udp.py` | opentrack-format sender | `core.net` + `OpenTrackPacket` |
| `sim.py` | direction-check sweep phases/poses and cockpit camera maths | `core.sim` |
| `gaze.py` | eye signals from blendshapes/iris landmarks, gaze estimate, eye-assisted look, head-turn compensation calibrator | `core.gaze` |
| `autocentre.py` | still-pose detection and gradual centre drift (PC only) | — |
| `hotkeys.py` | key / joystick polling with edge detection; Windows readers via ctypes | — |
| `profiles.py` | presets (driving, flight, passthrough), game → category table, per-game tuning files | — |
| `selfcheck.py` / `fixes.py` | diagnostics rows with fix actions (firewall rule, camera privacy, downloads, camera restart) | `core.diagnostics.SelfCheck` |
| `engine.py` | worker thread, idle resend, calibration phases, phone-over-webcam policy, `EngineState` for the UI | `app.tracking.TrackingEngine` |
| `app.py` | builds sources/outputs/discovery from the profile; reacts to profile changes | `AppGraph` + `MainViewModel` |
| `gui.py` / `cli.py` | Tkinter window (centre step → Track / Connect / Advanced) / console | Compose screens |

Threading: sources push `Frame`s from their threads into the engine queue; the engine thread is
the only one touching the pipeline and outputs; the UI polls an immutable `EngineState` every
50 ms and sends commands through the queue. Nothing blocks the camera thread on the game output.

Webcam frames are analysed un-mirrored (a raw webcam frame is not a mirror image), so
`mirrored=False` by default; the preview is flipped for display only.
