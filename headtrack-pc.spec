# PyInstaller spec: `pyinstaller headtrack-pc.spec` → dist/HeadTrackPC/HeadTrackPC.exe
# Run tools/fetch_models.py and tools/fetch_opentrack_libs.py first.
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
root = os.path.abspath(os.path.dirname(SPEC))
datas = [
    (os.path.join(root, "data", "games.csv"), "data"),
    (os.path.join(root, "headtrack_pc", "models"), os.path.join("headtrack_pc", "models")),
    (os.path.join(root, "headtrack_pc", "libs"), os.path.join("headtrack_pc", "libs")),
]
datas += collect_data_files("mediapipe")

a = Analysis(
    [os.path.join(root, "headtrack_pc", "__main__.py")],
    pathex=[root],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("mediapipe") + ["tkinter", "cv2"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "PyQt5", "PySide6", "IPython"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HeadTrackPC", console=False, icon=None)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="HeadTrackPC")
