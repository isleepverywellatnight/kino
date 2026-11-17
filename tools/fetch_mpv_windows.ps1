# Download the mpv build bundled with KINO for Windows into vendor\mpv-windows.
#
# The archive is pinned by name and SHA-256: a different file is refused. Only
# the player itself is kept (mpv.exe and the Direct3D shader compiler it
# loads). MANIFEST.json records the source and the SHA-256 of every file.
#
# Builds by shinchiro (github.com/shinchiro/mpv-winbuild-cmake), mirrored on
# SourceForge. mpv and FFmpeg are under GPL/LGPL: sources at mpv.io and ffmpeg.org.
#
# Usage: pwsh tools\fetch_mpv_windows.ps1 [-Archive path\to\mpv-x86_64-....7z]
# Needs 7-Zip (7z.exe; installed on GitHub's Windows runners).
param([string]$Archive)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$build = "20261018"
$name = "mpv-x86_64-$build-git-01e2533f31.7z"
$expected = "d7157567a2fd88e46f739d2e8b8e6c02d2ad812db04547b45c59a2800821991f"
$sources = @(
  "https://downloads.sourceforge.net/project/mpv-player-windows/64bit/$name",
  "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/$build/$name"
)
$keep = @("mpv.exe", "mpv.com", "d3dcompiler_43.dll")
$dest = "vendor\mpv-windows"

$work = Join-Path ([IO.Path]::GetTempPath()) ("kino-mpv-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $work | Out-Null
try {
  if ($Archive) {
    $source = $Archive
    if ((Get-FileHash $Archive -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw "$Archive n'a pas l'empreinte attendue." }
  } else {
    $Archive = Join-Path $work $name
    $errors = @()
    $source = $null
    foreach ($url in $sources) {
      try {
        Invoke-WebRequest -Uri $url -OutFile $Archive -UserAgent "KINO-build" -MaximumRedirection 10
      } catch {
        $errors += "${url}: $_"
        continue
      }
      $actual = (Get-FileHash $Archive -Algorithm SHA256).Hash.ToLower()
      if ($actual -eq $expected) { $source = $url; break }
      $errors += "${url}: SHA-256 $actual au lieu de $expected"
    }
    if (-not $source) { throw ("Impossible de récupérer mpv :`n  " + ($errors -join "`n  ")) }
  }

  $sevenZip = (Get-Command 7z, 7zz, 7za -ErrorAction SilentlyContinue | Select-Object -First 1).Source
  if (-not $sevenZip) {
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
      if ($base -and (Test-Path "$base\7-Zip\7z.exe")) { $sevenZip = "$base\7-Zip\7z.exe" }
    }
  }
  if (-not $sevenZip) { throw "7-Zip est introuvable : installez-le (7-zip.org) puis relancez." }
  $extracted = Join-Path $work "extracted"
  & $sevenZip e -y "-o$extracted" $Archive @keep | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "7-Zip a échoué ($LASTEXITCODE)" }

  if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
  New-Item -ItemType Directory -Path $dest | Out-Null
  $files = @()
  foreach ($file in $keep) {
    $path = Join-Path $extracted $file
    if (-not (Test-Path $path)) { throw "$file absent de $name" }
    Copy-Item $path (Join-Path $dest $file)
    $copied = Get-Item (Join-Path $dest $file)
    $files += [ordered]@{ name = $file; sha256 = (Get-FileHash $copied -Algorithm SHA256).Hash.ToLower(); bytes = $copied.Length }
  }
  $manifest = [ordered]@{
    source = $source; archive = $name; archive_sha256 = $expected
    note = "mpv and FFmpeg built by shinchiro/mpv-winbuild-cmake (GPL/LGPL). Sources: mpv.io, ffmpeg.org."
    files = $files
  }
  $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $dest "MANIFEST.json") -Encoding utf8
  $total = ($files | Measure-Object -Property bytes -Sum).Sum
  Write-Output ("mpv $build copié dans $dest ({0:N1} Mo)" -f ($total / 1e6))
} finally {
  Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
