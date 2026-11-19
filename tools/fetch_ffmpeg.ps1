# FFmpeg shipped with KINO for Windows: the build imageio-ffmpeg publishes
# (static, x64 or ARM64), pinned by version and SHA-256, unpacked into vendor\ffmpeg\ffmpeg.exe.
# Usage: pwsh tools\fetch_ffmpeg.ps1 [-Arch x64|arm64]
param([ValidateSet("x64", "arm64")][string]$Arch = "x64")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if ($Arch -eq "arm64") {
  $wheel = "imageio_ffmpeg-0.8.1-py3-none-win_arm64.whl"
  $expected = "0b7079e63dc34f97e43db5a5b7b9efb85edc1c6623313e0a4a17ebc7c2ce7a51"
  $url = "https://files.pythonhosted.org/packages/ee/39/ab95a2afd202772a69aab1fa5015f13b038f289c8617242cc5eebd0ec0e1/$wheel"
} else {
  $wheel = "imageio_ffmpeg-0.8.1-py3-none-win_amd64.whl"
  $expected = "f27ca4cb85e2d4946deedbce1a81245dfd942a2bcf94b53676fb3956aa95bf1b"
  $url = "https://files.pythonhosted.org/packages/82/0c/a1d49f3dd2688bd750b4986f8e271ad11f8e729a896070ea25f587b44da8/$wheel"
}
$work = Join-Path ([IO.Path]::GetTempPath()) ("kino-ffmpeg-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $work | Out-Null
try {
  $file = Join-Path $work "$wheel.zip"
  Invoke-WebRequest -Uri $url -OutFile $file -UserAgent "KINO-build"
  if ((Get-FileHash $file -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw "$wheel n'a pas l'empreinte attendue." }
  Expand-Archive -Path $file -DestinationPath (Join-Path $work "out")
  $binary = Get-ChildItem (Join-Path $work "out\imageio_ffmpeg\binaries") -Filter "ffmpeg-*.exe" | Select-Object -First 1
  if (-not $binary) { throw "ffmpeg absent de $wheel" }
  New-Item -ItemType Directory -Force -Path "vendor\ffmpeg" | Out-Null
  Copy-Item $binary.FullName "vendor\ffmpeg\ffmpeg.exe"
  & "vendor\ffmpeg\ffmpeg.exe" -version | Select-Object -First 1
} finally {
  Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
