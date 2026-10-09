# Uploads dist\HeadTrackPC to Steam with SteamPipe.
# Prerequisites (one-time):
#   1. Steamworks partner account with an App ID and one Windows depot.
#   2. Steamworks SDK unzipped somewhere; set $env:STEAMWORKS_SDK to that folder
#      (contains tools\ContentBuilder\builder\steamcmd.exe and redistributable_bin\win64\steam_api64.dll).
#   3. Fill YOUR_APP_ID / YOUR_DEPOT_ID in steam\app_build.vdf and steam\depot_build.vdf.
#   4. Build first: .\scripts\build.ps1
# Usage:  .\scripts\steam_upload.ps1 -Username <steam login>
param([Parameter(Mandatory=$true)][string]$Username)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sdk = $env:STEAMWORKS_SDK
if (-not $sdk) { throw "Set STEAMWORKS_SDK to the unzipped Steamworks SDK folder" }
$dist = Join-Path $root "dist\HeadTrackPC"
if (-not (Test-Path (Join-Path $dist "HeadTrackPC.exe"))) { throw "Build first: scripts\build.ps1" }
# The Steam API DLL ships with the build; the app loads it only when present.
Copy-Item (Join-Path $sdk "redistributable_bin\win64\steam_api64.dll") $dist -Force
# Never ship steam_appid.txt (it bypasses the Steam launch check); it is for local testing only.
Remove-Item (Join-Path $dist "steam_appid.txt") -ErrorAction SilentlyContinue
$steamcmd = Join-Path $sdk "tools\ContentBuilder\builder\steamcmd.exe"
& $steamcmd +login $Username +run_app_build (Join-Path $root "steam\app_build.vdf") +quit
Write-Host "Uploaded. Set the build live in Steamworks > SteamPipe > Builds."
