# Security

## Reporting a vulnerability

Please report it privately: **Security → Report a vulnerability** on this
repository. Do not open a public issue. You will get an answer as soon as
possible, and a fix in the next release.

## What KINO protects

- **Your keys** (debrid service, Trakt, TMDB) stay on your computer, in
  `~/.rd_cinehub_config.json`. They are never backed up, synced or sent anywhere
  but to the service they belong to.
- **Its local server** answers the KINO window on this computer. From another
  device on your network it answers only the phone remote, with the pairing
  token of its QR code, and the stream KINO sends to a TV you chose.
- **Updates** are installed only when their Ed25519 signature matches the key
  built into KINO, and on macOS after the new app passes its own self-check; the
  previous version is kept for a rollback.
- **Release files** are built by GitHub Actions from the commit they publish,
  with their SHA-256 in `SHA256SUMS.txt` and a build attestation (see the README).
- **The interface** runs no code fetched from the internet.

## What KINO cannot protect

KINO is not signed by Apple or Microsoft yet: macOS and Windows ask for
confirmation on first launch. Peer-to-peer playback shows your IP address to
other peers, as any BitTorrent client does; KINO says so before you turn it on.
