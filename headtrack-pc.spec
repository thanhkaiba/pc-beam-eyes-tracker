# PyInstaller spec: `pyinstaller headtrack-pc.spec` → dist/HeadTrackPC/ with
#   HeadTrackPC.exe          the window (no console)
#   HeadTrackPC-console.exe  same program with a console, for --cli and for seeing errors
# Run tools/fetch_models.py and tools/fetch_opentrack_libs.py first.
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
    [os.path.join(root, "headtrack_pc", "__main__.py")],
    pathex=[root],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules("mediapipe") + ["tkinter", "cv2", "numpy"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "jupyter", "notebook", "torch", "tensorflow"],
)
pyz = PYZ(a.pure)
exe_gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HeadTrackPC", console=False)
exe_cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HeadTrackPC-console", console=True)
coll = COLLECT(exe_gui, exe_cli, a.binaries, a.datas, strip=False, upx=False, name="HeadTrackPC")
