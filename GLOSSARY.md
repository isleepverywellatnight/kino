# KINO Media Streaming

The core media streaming and playback system for KINO, providing catalog browsing, torrent stream discovery, debrid acceleration, and native video player control.

## Language

**Stream Query**:
The target media specification (IMDb ID, media type, season, episode, title, release year, runtime) required to locate matching media streams.
_Avoid_: Search params, query dict, torrent query

**Stream**:
A discovered media source candidate with evaluated metadata (resolution quality, codecs, languages, file size, instant cache status, and cinephile score).
_Avoid_: Result, item, torrent entry, magnet link

**Playable Stream**:
A stream that has been unrestricted through a debrid provider into a direct, high-speed HTTP URL ready for player ingestion.
_Avoid_: Download URL, direct link, debrided link, resolved link

**Debrid Provider**:
An external premium multi-hoster service (Real-Debrid, AllDebrid, TorBox) used to inspect instant torrent cache and generate direct streaming URLs.
_Avoid_: Debrid account, resolver, hoster
