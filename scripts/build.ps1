# Windows build (PowerShell, from the repo root):  .\scripts\build.ps1
# Produces dist\HeadTrackPC\HeadTrackPC.exe (portable folder), dist\HeadTrackPC-<version>-portable.zip
# and, when Inno Setup 6 is installed, dist\HeadTrackPC-<version>-Setup.exe. Same steps as
# .github/workflows/windows.yml.
$ErrorActionPreference = "Stop"
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python tools\fetch_models.py
python tools\fetch_opentrack_libs.py
python tools\fetch_assets.py
python -m unittest discover -s tests -t .
pyinstaller --noconfirm headtrack-pc.spec
$version = python -c "import headtrack_pc; print(headtrack_pc.__version__)"
$p = Start-Process -FilePath .\dist\HeadTrackPC\HeadTrackPC.exe -ArgumentList '--selftest' -NoNewWindow -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "self-test of the built exe failed ($($p.ExitCode))" }
Compress-Archive -Force -Path dist\HeadTrackPC -DestinationPath "dist\HeadTrackPC-$version-portable.zip"
$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    & $iscc "/DAppVersion=$version" installer\HeadTrackPC.iss
    Write-Host "Built dist\HeadTrackPC-$version-Setup.exe and dist\HeadTrackPC-$version-portable.zip"
} else {
    Write-Host "Built dist\HeadTrackPC-$version-portable.zip (install Inno Setup 6 for the Setup.exe)"
}
