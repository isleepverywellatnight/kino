# Install or update KINO on Windows 10/11 (64-bit), from PowerShell:
#   irm https://raw.githubusercontent.com/isleepverywellatnight/kino/main/packaging/install.ps1 | iex
# Downloads the latest release, checks its SHA-256, installs it for the current
# user (no administrator rights) and starts KINO. Nothing else to install.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is much faster without its progress bar

# GitHub needs TLS 1.2, which older Windows PowerShell 5.1 does not always enable.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$base = "https://github.com/isleepverywellatnight/kino/releases/latest/download"
if (-not [Environment]::Is64BitOperatingSystem) { throw "KINO demande Windows 64 bits." }

$work = Join-Path ([IO.Path]::GetTempPath()) ("kino-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    Write-Host "Téléchargement de KINO…"
    $setup = Join-Path $work "KINO-Windows.exe"
    Invoke-WebRequest -UseBasicParsing -Uri "$base/KINO-Windows.exe" -OutFile $setup
    # Saved to a file: GitHub serves it as binary data, which .Content would return as bytes.
    $sums = Join-Path $work "SHA256SUMS.txt"
    Invoke-WebRequest -UseBasicParsing -Uri "$base/SHA256SUMS.txt" -OutFile $sums
    $line = Get-Content $sums | Where-Object { $_ -match '\s\*?KINO-Windows\.exe\s*$' } | Select-Object -First 1
    if (-not $line) { throw "Empreinte de KINO-Windows.exe introuvable." }
    $expected = ($line -split '\s+')[0].ToLower()
    $actual = (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLower()
    if ($expected -ne $actual) { throw "Le fichier téléchargé est incomplet ou modifié." }

    $running = Get-Process -Name KINO -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host "Fermeture de KINO…"
        $running | ForEach-Object { $_.CloseMainWindow() | Out-Null }
        Start-Sleep -Seconds 3
        Get-Process -Name KINO -ErrorAction SilentlyContinue | Stop-Process -Force
    }

    Write-Host "Installation…"
    $p = Start-Process -FilePath $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "L'installation a échoué (code $($p.ExitCode))." }

    $exe = Join-Path $env:LOCALAPPDATA "Programs\KINO\KINO.exe"
    Write-Host "KINO est installé."
    Start-Process -FilePath $exe
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
