# Assemble dist\KINO (the folder the installer ships) from the Rust build.
# Needs: cargo build --release --locked -p kino-desktop, vendor\mpv-windows
# (tools/fetch_mpv_windows.py) and an FFmpeg executable to ship.
# Usage: pwsh packaging\build_windows_app.ps1 C:\path\to\ffmpeg.exe
param([Parameter(Mandatory = $true)][string]$Ffmpeg)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$binary = "target\release\kino-desktop.exe"
if (-not (Test-Path $binary)) { throw "$binary is missing: run cargo build --release --locked -p kino-desktop" }
if (-not (Test-Path "vendor\mpv-windows\mpv.exe")) { throw "vendor\mpv-windows is missing: run tools/fetch_mpv_windows.py" }

$app = "dist\KINO"
if (Test-Path $app) { Remove-Item $app -Recurse -Force }
New-Item -ItemType Directory -Force -Path "$app\assets", "$app\mpv" | Out-Null
Copy-Item $binary "$app\KINO.exe"
Copy-Item web "$app\web" -Recurse
Copy-Item assets\kino.ico, assets\kino.png, assets\kino_bridge.lua, assets\update_public_key.hex "$app\assets\"
Copy-Item vendor\mpv-windows\* "$app\mpv\" -Recurse
Copy-Item $Ffmpeg "$app\ffmpeg.exe"
Write-Output "KINO : $app"
