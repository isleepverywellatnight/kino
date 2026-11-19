# Changelog

## 2.1.1

- Linux: the AppImage carries JACK's client library, which FFmpeg needs to
  start: on a system without JACK, KINO's FFmpeg (video details, intros)
  did not run.

## 2.1.0

- Linux: KINO comes as an AppImage (x86_64; Ubuntu 22.04, Debian 12, Fedora 36
  or later). Make it executable and open it: nothing else to install. Videos
  play in KINO's window, or in mpv if it is installed. KINO announces new
  versions; download the new AppImage.
- Linux: KINO's window has its own bar, as on Windows: it moves the window,
  a double click maximizes it, and its buttons minimize, maximize and close.
- The interface says « this computer » instead of « this Mac » on Windows and
  Linux: offline files and the space left, where suggestions and statistics
  are computed, the TV on the same network, the backups.

## 2.0.1

- Translations: texts that showed wrong in some languages now read right. The
  average rating of a list (« IMDb 8,2 en moyenne ») is translated in German,
  Spanish, Italian, Portuguese, Russian and Chinese; a French plural « s » no
  longer sticks to a word (« Titel gesehens »); « ▶ Lancer S02E04 » is
  translated; the source filter reads « Show only SDR sources » in English,
  and « hevc » no longer turns into « Std.evc » in German or « чevc » in Russian.
- An apostrophe or a « & » in a subtitle's name, a torrent's status or a
  title no longer shows as « &#39; » or « &amp; ».
- Each translated text is found by its key: a change to a French sentence no
  longer loses its translations.

## 2.0.0

- KINO is now written in Rust from end to end: the app no longer carries
  Python. Your library, history, settings and keys are kept as they are, and
  an update from 1.9.2 installs by itself.
- Windows: the native player (libmpv) plays inside KINO's window, with
  KINO's controls over the video.
- Windows: the window has no frame of its own. It maximizes within the screen
  without covering the taskbar, a double click on the bar maximizes it, and
  dragging a maximized window gives it back its size under the cursor.
- A new look for windows, messages and empty screens, and small text made
  easier to read.
- Windows and Linux: the services' keys, the TMDB key and the Trakt tokens are
  also kept in the system's keychain.
- Google Drive sync no longer copies the Trakt tokens to other computers.
- The page runs only KINO's own scripts, and what services send is shown as
  text, never as markup.
- Peer-to-peer: a torrent is ready before its files are chosen.
- Suggestions come faster.

## 1.9.2

- Windows: fixed a crash (access violation) that could happen at startup or
  while the window was moved, resized or maximized.
- Faster HTTPS requests: one SSL context is built once and shared.

## 1.9.1

- Updates: KINO also looks for a new version every six hours, and
  Settings → Updates can turn the automatic check off.
- Releases: each file comes with a GitHub build attestation.

## 1.9.0

- Manga: the catalogue comes from AniList (search, trending and ongoing series,
  most popular, manhwa and webtoons). Chapters are gathered from several sites
  and merged by number, in the reading language when available and in English
  otherwise. Complete volumes can be downloaded by torrent and read offline.

## 1.8.2

- Releases and in-app updates come from this repository.

## 1.8.1

- Messages from KINO's server are translated like the rest of the interface.
- Releases include the signed packages that KINO installs by itself.

## 1.8.0

- Peer-to-peer settings: upload and download limits, encrypted connections only,
  listening port, and the temporary files with a button to clear them.
- Peer-to-peer playback no longer sends incomplete pieces to the player.
- Add-ons are named « Addons » in the interface.

## 1.7.0

- Manga tab: series from MangaDex and your own CBZ files and image folders, with
  a reader that turns pages in the Japanese direction and remembers where you
  stopped.

## 1.6.0

- Without a debrid service: add-ons for more sources and catalogues, and
  peer-to-peer playback after an explicit consent.
