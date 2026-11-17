# Assemble dist\KINO (the folder the installer ships) from the Rust build.
# Needs: cargo build --release --locked -p kino-desktop, vendor\mpv-windows
# (tools\fetch_mpv_windows.ps1) and vendor\ffmpeg (tools\fetch_ffmpeg.ps1).
# Usage: pwsh packaging\build_windows_app.ps1 [-Ffmpeg C:\path\to\ffmpeg.exe]
param([string]$Ffmpeg = "vendor\ffmpeg\ffmpeg.exe")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$binary = "target\release\kino-desktop.exe"
if (-not (Test-Path $binary)) { throw "$binary is missing: run cargo build --release --locked -p kino-desktop" }
if (-not (Test-Path "vendor\mpv-windows\mpv.exe")) { throw "vendor\mpv-windows is missing: run tools\fetch_mpv_windows.ps1" }
if (-not (Test-Path $Ffmpeg)) { throw "$Ffmpeg is missing: run tools\fetch_ffmpeg.ps1" }

$app = "dist\KINO"
if (Test-Path $app) { Remove-Item $app -Recurse -Force }
New-Item -ItemType Directory -Force -Path "$app\assets", "$app\mpv" | Out-Null
Copy-Item $binary "$app\KINO.exe"
Copy-Item web "$app\web" -Recurse
Copy-Item assets\kino.ico, assets\kino.png, assets\kino_bridge.lua, assets\update_public_key.hex "$app\assets\"
Copy-Item vendor\mpv-windows\* "$app\mpv\" -Recurse
Copy-Item $Ffmpeg "$app\ffmpeg.exe"
Write-Output "KINO : $app"
