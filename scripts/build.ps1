# Windows build: creates dist\HeadTrackPC\HeadTrackPC.exe
# Usage (PowerShell, from the repo root):  .\scripts_build.ps1
$ErrorActionPreference = "Stop"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python tools\fetch_models.py
python tools\fetch_opentrack_libs.py
python -m unittest discover -s tests -t .
pyinstaller --noconfirm headtrack-pc.spec
Write-Host "Built dist\HeadTrackPC\HeadTrackPC.exe"
