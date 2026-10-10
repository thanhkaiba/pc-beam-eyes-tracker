# PyInstaller spec: `pyinstaller headtrack-pc.spec` → dist/HeadTrackPC/HeadTrackPC.exe, one
# windowed exe. `HeadTrackPC.exe --cli` / `--selftest` / `--version` attach to the console they
# were started from (launcher.py), so no second console exe is needed.
# Run tools/fetch_models.py and tools/fetch_opentrack_libs.py first.
# Do NOT exclude matplotlib or PIL: `import mediapipe` imports both (drawing_utils); excluding
# them made the frozen app report "MediaPipe is not installed".
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = os.path.abspath(os.path.dirname(SPEC))
datas = [
    (os.path.join(root, "data", "games.csv"), "data"),
    (os.path.join(root, "headtrack_pc", "models"), os.path.join("headtrack_pc", "models")),
    (os.path.join(root, "headtrack_pc", "libs"), os.path.join("headtrack_pc", "libs")),
]
datas += collect_data_files("mediapipe")
binaries = collect_dynamic_libs("mediapipe")

a = Analysis(
    [os.path.join(root, "launcher.py")],
    pathex=[root],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules("mediapipe") + ["tkinter", "cv2", "numpy"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "jupyter", "notebook", "torch", "tensorflow"],
)
pyz = PYZ(a.pure)
icon = os.path.join(root, "installer", "headtrack.ico")
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HeadTrackPC", console=False,
          icon=icon if os.path.isfile(icon) else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="HeadTrackPC")
