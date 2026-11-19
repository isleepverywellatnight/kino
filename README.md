# KINO

**English** · [Français](docs/README.fr.md) · [Deutsch](docs/README.de.md) · [Español](docs/README.es.md) · [Italiano](docs/README.it.md) · [Português](docs/README.pt.md) · [Русский](docs/README.ru.md) · [中文](docs/README.zh.md) · [日本語](docs/README.ja.md)

KINO is a desktop app for macOS and Windows that brings together a catalogue of
films, series and anime, tracking of what you watch, and playback, through your
own account with a debrid service (Real-Debrid, AllDebrid, TorBox, Debrid-Link,
Premiumize).

The interface is available in English, French, German, Spanish, Italian,
Portuguese, Russian, Chinese and Japanese: KINO follows your computer's
language, and you can change it in Settings → Application → Interface language.

## Disclaimer

KINO does not host, provide or distribute any content. It queries sources the
user chooses and plays what the user's own debrid service returns. Each user is
solely responsible for what they watch and for respecting copyright law in
their country.

## What KINO does

- **Catalogue**: films, series and anime, with details, cast, trailers, and each
  title under its original name.
- **Without a debrid service**: add add-ons (more sources, and their
  catalogues in Explore), and play torrents peer to peer once you have agreed
  to what it means (your IP address is visible, and you share while you watch).
- **Manga**: the whole catalogue and the series coming out now, with every
  chapter gathered from several sites (in your language when there is one, else
  in English), complete HD volumes by torrent, and your own CBZ files and image
  folders; read page by page or as one long scroll, in the Japanese direction if
  you like. KINO remembers where you stopped.
- **Playback**: built-in video player based on mpv (HDR, subtitles, audio
  tracks, intro skipping).
- **Tracking**: My List, in progress, watched, my series, release calendar,
  one-tap ratings.
- **Suggestions**: "For you" and "If you liked", computed on your computer from
  what you have watched and rated.
- **Anime**: rows from AniList, weekly schedule, a saga's journey in release
  order.
- **Other services**: Letterboxd import, Trakt, Discord presence.
- **Together and elsewhere**: Watch Party, playback on a TV on your local
  network, remote control from a phone.
- **Offline**: download a film or a season to watch without internet.
- **Your data**: kept on your computer, with a daily backup and optional sync
  through Google Drive.

## Download KINO

| System | Download |
|---|---|
| **macOS** — Apple Silicon Mac (M1 or later), macOS 14 or later | [**KINO-macOS.dmg**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-macOS.dmg) |
| **Windows** — Windows 10 or 11, 64-bit | [**KINO-Windows.exe**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows.exe) |
| **Linux** — x86_64: Ubuntu 22.04, Debian 12, Fedora 36 or later | [**KINO-Linux-x86_64.AppImage**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Linux-x86_64.AppImage) |

KINO contains everything it needs: video player, FFmpeg, interface. There is
nothing else to install. Then paste your debrid service key in Settings.

- **macOS**: open the `.dmg` file and drag KINO into Applications.
- **Windows**: run `KINO-Windows.exe`. KINO installs in one click, without
  administrator rights, then opens.
- **Linux**: make `KINO-Linux-x86_64.AppImage` executable (Properties →
  Permissions, or `chmod +x`), then open it. Videos play in KINO's window, or
  in mpv if it is installed. KINO announces new versions: download the new
  AppImage.

Later versions install from KINO itself: **Settings → Updates**.

### Install with a single command

This command downloads the latest version, checks the file, installs KINO and
opens it. It also updates it.

macOS (Terminal app):

```bash
curl -fsSL https://github.com/isleepverywellatnight/kino/releases/latest/download/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://github.com/isleepverywellatnight/kino/releases/latest/download/install.ps1 | iex
```

### If the system blocks the first launch

KINO is not yet signed by Apple or Microsoft. After a download from the browser:

- **macOS**: if KINO refuses to open, go to System Settings → Privacy &
  Security, then click **Open Anyway**.
- **Windows**: if "Windows protected your PC" appears, click **More info**,
  then **Run anyway**.

The commands above avoid both messages.

## Verify a download

Every release file is built by GitHub Actions from the commit the release
names. Its SHA-256 is in `SHA256SUMS.txt`, and, from the release after 1.9.0, a
build attestation tells which workflow and which commit made it:

```bash
gh attestation verify KINO-macOS.dmg -R isleepverywellatnight/kino
```

## Privacy

KINO has no server of its own and collects nothing. It talks directly to the
services you connect (your debrid service, Trakt, Discord, Google Drive), the
public catalogues it reads (such as Cinemeta, AniList, MangaDex, and TMDB with
your key), the sources it searches (indexers, your add-ons, manga sites),
BitTorrent peers when peer-to-peer playback is on, and GitHub to look for a new
version at startup and every six hours. Settings → Updates turns that check off.

## Where your data lives

- `~/.kino_data.db`: library, history, ratings and cache.
- `~/.rd_cinehub_config.json`: settings and service keys.
- `~/Library/Application Support/KINO` (macOS) or `%APPDATA%\KINO` (Windows):
  backups, error log. Pending updates are in `%LOCALAPPDATA%\KINO\Updates` on
  Windows.

Keys stay on your computer: they are neither backed up nor synced. Uninstalling
KINO does not delete this data.

## Repository layout

| Folder | Contents |
|---|---|
| `crates/` | KINO's code, in Rust: `kino` (the server) and `kino-desktop` (the app) |
| `web/` | Interface (HTML, CSS, JavaScript) |
| `assets/` | Icons and files shipped with the app |
| `packaging/` | App builds, installers, install scripts |
| `tools/` | Build and release tools |
| `tests/` | Tests |
| `docs/` | The READMEs in other languages |

To build KINO or publish a version, see [packaging/README.md](packaging/README.md).

## License

KINO is released under the MIT license (see [LICENSE](LICENSE)). The
`web/js/vendor` folder contains PeerJS and QRCode.js, each under its own MIT
license. mpv, libmpv and FFmpeg, included in the apps, are under the GPL or
LGPL.
