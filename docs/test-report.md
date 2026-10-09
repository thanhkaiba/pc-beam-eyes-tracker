# Test report

| Date | What | Where | Result |
| --- | --- | --- | --- |
| 2026-10-09 | `python -m unittest discover -s tests -t .` (pose maths incl. camera-offset removal, calibration, mapping, filters, face-loss, pipeline, profile codec, packet codecs incl. discovery, phone receiver + ping + discovery over loopback, discovery responder, UDP output, FT_SharedMem layout/handshake, game table, engine with fake source/outputs) | Linux, Python 3.13, mediapipe 1.1.0 installed but not exercised | **68 passed** |
| 2026-10-09 | Tkinter window under Xvfb with a fake camera: centre step → calibration → tabs, Apply on every Advanced section, Connect tab text | Linux, Python 3.12 | passed (smoke) |
| — | Webcam + MediaPipe on a real camera | Windows | **not run** |
| — | Game reads the pose through NPClient.dll / freetrackclient.dll | Windows + game | **not run** |
| — | Phone finds the PC and drives the game | Windows + Android | **not run** |

The MediaPipe Python API used (`FaceLandmarkerOptions(... running_mode=LIVE_STREAM, output_facial_transformation_matrixes=True, result_callback=...)`,
`FaceLandmarker.create_from_options`, `detect_async(image, ts_ms)`, `result.facial_transformation_matrixes` as 4×4 numpy arrays) was checked by
introspection against mediapipe 1.1.0 on 2026-10-09; it is identical to the 0.10.x API the model card documents.
