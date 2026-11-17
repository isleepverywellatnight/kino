# FFmpeg shipped with KINO for Windows: the build imageio-ffmpeg publishes
# (static, x64), pinned by version and SHA-256, unpacked into vendor\ffmpeg\ffmpeg.exe.
# Usage: pwsh tools\fetch_ffmpeg.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$wheel = "imageio_ffmpeg-0.8.1-py3-none-win_amd64.whl"
$expected = "f27ca4cb85e2d4946deedbce1a81245dfd942a2bcf94b53676fb3956aa95bf1b"
$url = "https://files.pythonhosted.org/packages/82/0c/a1d49f3dd2688bd750b4986f8e271ad11f8e729a896070ea25f587b44da8/$wheel"
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
