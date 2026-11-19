## Download KINO {{VERSION}}

| System | File |
|---|---|
| **macOS** (Apple Silicon Mac, macOS 14 or later) | [KINO-macOS.dmg](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-macOS.dmg) |
| **Windows** (10 or 11, 64-bit) | [KINO-Windows.exe](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows.exe) |
| **Linux** (x86_64: Ubuntu 22.04, Debian 12, Fedora 36 or later) | [KINO-Linux-x86_64.AppImage](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Linux-x86_64.AppImage) |

Everything is included: nothing else to install. Later versions install from KINO (Settings → Updates); on Linux, KINO announces them and you download the new AppImage.

**Even simpler**: one command installs or updates KINO, without any system warning.

- macOS (Terminal app): `curl -fsSL https://github.com/isleepverywellatnight/kino/releases/latest/download/install.sh | bash`
- Windows (PowerShell): `irm https://github.com/isleepverywellatnight/kino/releases/latest/download/install.ps1 | iex`

<details><summary>First launch after a download from the browser</summary>

KINO is not yet signed by Apple or Microsoft.

- **macOS**: open the .dmg and drag KINO into Applications. If macOS refuses to open it: System Settings → Privacy & Security → **Open Anyway**.
- **Windows**: if "Windows protected your PC" appears, click **More info**, then **Run anyway**. The installation then takes one click, without administrator rights.
- **Linux**: make the AppImage executable (Properties → Permissions, or `chmod +x KINO-Linux-x86_64.AppImage`), then open it.
</details>

SHA-256 checksums: `SHA256SUMS.txt`.
