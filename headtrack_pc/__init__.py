"""HeadTrack PC: webcam (or phone) head tracking that feeds games directly.

The package mirrors the Android app's `core` library (same pose convention, calibration,
mapping, filters, face-loss policy and packet formats) and adds what only a PC can do: a
webcam tracker, a receiver for the phone's packets, and a freetrack / TrackIR output so no
separate opentrack installation is needed.
"""

__version__ = "0.1.0"
