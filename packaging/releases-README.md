# KINO

KINO is a desktop app for macOS and Windows that brings together a catalogue of
films, series and anime, tracking of what you watch, and playback, through your
own account with a debrid service (Real-Debrid, AllDebrid, TorBox, Debrid-Link,
Premiumize). Its interface is in English, French, German, Spanish, Italian,
Portuguese, Russian, Chinese and Japanese.

This repository only holds KINO's releases.

## Download

| System | Download |
|---|---|
| **macOS** — Apple Silicon Mac (M1 or later), macOS 14 or later | [**KINO-macOS.dmg**](https://github.com/isleepverywellatnight/kino-releases/releases/latest/download/KINO-macOS.dmg) |
| **Windows** — Windows 10 or 11, 64-bit | [**KINO-Windows.exe**](https://github.com/isleepverywellatnight/kino-releases/releases/latest/download/KINO-Windows.exe) |

Everything is included: nothing else to install. Then paste your debrid service
key in Settings. Later versions install from KINO itself (Settings → Updates).

- **macOS**: open the `.dmg` file and drag KINO into Applications.
- **Windows**: run `KINO-Windows.exe`. It installs in one click, without
  administrator rights, then opens.

### Install with a single command

Downloads the latest version, checks it, installs KINO and opens it; it also
updates it.

macOS (Terminal app):

```bash
curl -fsSL https://github.com/isleepverywellatnight/kino-releases/releases/latest/download/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://github.com/isleepverywellatnight/kino-releases/releases/latest/download/install.ps1 | iex
```

### If the system blocks the first launch

KINO is not yet signed by Apple or Microsoft. After a download from the browser:

- **macOS**: System Settings → Privacy & Security → **Open Anyway**.
- **Windows**: on "Windows protected your PC", click **More info**, then
  **Run anyway**.

The commands above avoid both messages.

## Disclaimer

KINO does not host, provide or distribute any content. It queries sources the
user chooses and plays what the user's own debrid service returns. Each user is
solely responsible for what they watch and for respecting copyright law in
their country.
