# Download the mpv builds bundled with KINO for Windows:
#   vendor\mpv-windows     mpv.exe, the external player (and the Direct3D shader
#                          compiler it loads);
#   vendor\libmpv-windows  libmpv-2.dll, the native player drawn in KINO's window
#                          (crates/kino-desktop/src/win_mpv.rs).
#
# Each archive is pinned by name and SHA-256: a different file is refused. Only
# the files KINO uses are kept. MANIFEST.json records the source and the SHA-256
# of every file.
#
# Builds by shinchiro (github.com/shinchiro/mpv-winbuild-cmake), mirrored on
# SourceForge; both archives come from the same build. mpv and FFmpeg are under
# GPL/LGPL: sources at mpv.io and ffmpeg.org.
#
# -Arch arm64 takes the builds for Windows on ARM (KINO-Windows-arm64.exe),
# published on shinchiro's GitHub releases only.
#
# Usage: pwsh tools\fetch_mpv_windows.ps1 [-Arch x64|arm64] [-Archive mpv-....7z] [-DevArchive mpv-dev-....7z]
# Needs 7-Zip (7z.exe; installed on GitHub's Windows runners) or Windows' own tar.exe.
param([string]$Archive, [string]$DevArchive, [ValidateSet("x64", "arm64")][string]$Arch = "x64")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$build = "20261018"
$commit = "git-01e2533f31"
$archives = if ($Arch -eq "arm64") { @(
  [ordered]@{
    name = "mpv-aarch64-$build-$commit.7z"
    sha256 = "736934c6f960f937851577d21f4d57695ff930a0b9aebe5521112f1c74d4c0d0"
    mirror = "arm64"; given = $Archive
    keep = @("mpv.exe", "mpv.com"); dest = "vendor\mpv-windows"
  },
  [ordered]@{
    name = "mpv-dev-aarch64-$build-$commit.7z"
    sha256 = "062b07022d1fadefd341485602658a51d973c216bb4282859e10daa09ad55478"
    mirror = "libmpv-arm64"; given = $DevArchive
    keep = @("libmpv-2.dll"); dest = "vendor\libmpv-windows"
  }
) } else { @(
  [ordered]@{
    name = "mpv-x86_64-$build-$commit.7z"
    sha256 = "d7157567a2fd88e46f739d2e8b8e6c02d2ad812db04547b45c59a2800821991f"
    mirror = "64bit"; given = $Archive
    keep = @("mpv.exe", "mpv.com", "d3dcompiler_43.dll"); dest = "vendor\mpv-windows"
  },
  [ordered]@{
    name = "mpv-dev-x86_64-$build-$commit.7z"
    sha256 = "d7f5621f42b6e161fe032df914a10bdece28a89df3e57c6c3e16a5a250929b5e"
    mirror = "libmpv"; given = $DevArchive
    keep = @("libmpv-2.dll"); dest = "vendor\libmpv-windows"
  }
) }

function Get-Archive($spec, $work) {
  if ($spec.given) {
    if ((Get-FileHash $spec.given -Algorithm SHA256).Hash.ToLower() -ne $spec.sha256) { throw "$($spec.given) n'a pas l'empreinte attendue." }
    return @{ path = $spec.given; source = $spec.given }
  }
  $path = Join-Path $work $spec.name
  $errors = @()
  foreach ($url in @(
      "https://downloads.sourceforge.net/project/mpv-player-windows/$($spec.mirror)/$($spec.name)",
      "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/$build/$($spec.name)")) {
    try {
      Invoke-WebRequest -Uri $url -OutFile $path -UserAgent "KINO-build" -MaximumRedirection 10
    } catch {
      $errors += "${url}: $_"
      continue
    }
    $actual = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    if ($actual -eq $spec.sha256) { return @{ path = $path; source = $url } }
    $errors += "${url}: SHA-256 $actual au lieu de $($spec.sha256)"
  }
  throw ("Impossible de récupérer $($spec.name) :`n  " + ($errors -join "`n  "))
}

function Expand-Kept($archive, $keep, $into) {
  $sevenZip = (Get-Command 7z, 7zz, 7za -ErrorAction SilentlyContinue | Select-Object -First 1).Source
  if (-not $sevenZip) {
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
      if ($base -and (Test-Path "$base\7-Zip\7z.exe")) { $sevenZip = "$base\7-Zip\7z.exe" }
    }
  }
  New-Item -ItemType Directory -Force -Path $into | Out-Null
  if ($sevenZip) {
    & $sevenZip e -y "-o$into" $archive @keep | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "7-Zip a échoué ($LASTEXITCODE)" }
  } else {
    # Windows 10 and later: tar.exe (libarchive) reads 7z.
    & "$env:SystemRoot\System32\tar.exe" -xf $archive -C $into @keep
    if ($LASTEXITCODE -ne 0) { throw "tar a échoué ($LASTEXITCODE) ; installez 7-Zip (7-zip.org) puis relancez." }
  }
}

$work = Join-Path ([IO.Path]::GetTempPath()) ("kino-mpv-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $work | Out-Null
try {
  foreach ($spec in $archives) {
    $got = Get-Archive $spec $work
    $extracted = Join-Path $work ("extracted-" + $spec.mirror)
    Expand-Kept $got.path $spec.keep $extracted

    $dest = $spec.dest
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    New-Item -ItemType Directory -Path $dest | Out-Null
    $files = @()
    foreach ($file in $spec.keep) {
      $path = Join-Path $extracted $file
      if (-not (Test-Path $path)) { throw "$file absent de $($spec.name)" }
      Copy-Item $path (Join-Path $dest $file)
      $copied = Get-Item (Join-Path $dest $file)
      $files += [ordered]@{ name = $file; sha256 = (Get-FileHash $copied -Algorithm SHA256).Hash.ToLower(); bytes = $copied.Length }
    }
    $manifest = [ordered]@{
      source = $got.source; archive = $spec.name; archive_sha256 = $spec.sha256
      note = "mpv and FFmpeg built by shinchiro/mpv-winbuild-cmake (GPL/LGPL). Sources: mpv.io, ffmpeg.org."
      files = $files
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $dest "MANIFEST.json") -Encoding utf8
    $total = 0
    foreach ($f in $files) { $total += $f.bytes }
    Write-Output ("{0} copié dans {1} ({2:N1} Mo)" -f $spec.name, $dest, ($total / 1e6))
  }
} finally {
  Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
