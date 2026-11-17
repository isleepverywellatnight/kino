# Routes du serveur

Le serveur local de KINO (`crates/kino`) répond à l'interface. Chaque thème a
son fichier dans `crates/kino/src/routes/`, avec une fonction par adresse ;
`routes::router()` les réunit. `server.rs` garde :

- le contrôle d'accès (`access.rs`) : depuis le réseau local, seulement la
  télécommande avec son jeton et le flux de la TV en cours ;
- la page et ses fichiers (`/`, `/index.html`, `/icons.svg`, `/css/…`, `/js/…`) ;
- la réponse « Route introuvable ».

Les routes liées à la fenêtre (lecteurs, télécommande, TV, P2P, notifications,
diagnostic) sont servies quand la fenêtre tourne dans le même programme
(`kino-desktop`).

## Catalogue et fiches

`crates/kino/src/routes/titles.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/catalog` | `catalog_page` |
| GET | `/api/meta` | `meta` |
| GET | `/api/series-meta` | `series_meta` |
| GET | `/api/search-suggest` | `suggest` |
| GET | `/api/search` | `search` |
| GET | `/api/people` | `people` |
| POST | `/api/french-titles` | `french_titles` |
| POST | `/api/original-titles` | `original_titles` |
| GET | `/api/episodes-fr` | `episodes_fr` |
| GET | `/api/person` | `person` |
| POST | `/api/history-record` | `history_record` |
| POST | `/api/watched-toggle` | `watched_toggle` |

## Sources et débridage

`crates/kino/src/routes/sources.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/torrents` | `torrents` |
| POST | `/api/debrid` | `debrid` |
| POST | `/api/debrid-status` | `debrid_status` |
| GET | `/api/rd-history` | `rd_history` |
| POST | `/api/rd-delete` | `rd_delete` |
| POST | `/api/rd-cleanup` | `rd_cleanup` |
| POST | `/api/cloud-titles` | `cloud_titles` |
| GET | `/api/ad-dl` | `ad_dl` |
| GET | `/api/torbox-dl` | `torbox_dl` |

## Lecture

`crates/kino/src/routes/play.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| POST | `/api/one-click-play` | `one_click_play` |
| POST | `/api/mpv` | `external_player` |
| POST | `/api/vlc` | `external_player` |
| GET | `/api/auto-stream` | `auto_stream` |
| POST | `/api/playback-feedback` | `feedback` |
| GET | `/api/subtitles` | `subtitles` |
| GET | `/api/intro-times` | `intro_times` |
| GET | `/api/trailer` | `trailer` |
| GET | `/api/subtitle-cues` | `subtitle_cues` |

## Lecteur natif (macOS)

`crates/kino/src/routes/native.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/native-player/state` | `state` |
| POST | `/api/native-player/open` | `open` |
| POST | `/api/native-player/command` | `command` |
| POST | `/api/native-player/close` | `close` |
| POST | `/api/native-player/screenshot` | `screenshot` |
| POST | `/api/native-player/subtitle` | `subtitle` |

## Séries

`crates/kino/src/routes/series.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/new-episodes` | `new_episodes` |
| GET | `/api/my-series` | `my_series` |
| GET | `/api/calendar` | `calendar` |
| GET | `/api/user-lists` | `user_lists` |
| GET | `/api/stats` | `stats` |
| GET | `/api/community/curated` | `curated` |
| GET | `/api/letterboxd/custom-lists` | `custom_lists` |
| POST | `/api/import-letterboxd` | `import_letterboxd` |
| POST | `/api/letterboxd/import-list` | `import_list` |
| POST | `/api/letterboxd/delete-list` | `delete_list` |

## Suggestions

`crates/kino/src/routes/suggestions.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/for-you` | `for_you` |
| GET | `/api/related` | `related` |
| POST | `/api/for-you/not-for-me` | `not_for_me` |
| POST | `/api/for-you/reset` | `reset` |
| GET | `/api/for-you/status` | `status` |

## Anime

`crates/kino/src/routes/anime.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/anime/home` | `home` |
| GET | `/api/anime/week` | `week` |
| POST | `/api/anime/open` | `open` |
| GET | `/api/anime/journey` | `get_journey` |
| POST | `/api/anime/journey/resolve` | `resolve` |
| POST | `/api/anime/journey/order` | `order` |

## Bibliothèque

`crates/kino/src/routes/library.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| POST | `/api/watchlist` | `watchlist` |
| POST | `/api/history-remove` | `history_remove` |
| POST | `/api/rating` | `rating` |
| POST | `/api/tonight` | `tonight` |
| POST | `/api/tonight/feedback` | `tonight_feedback` |
| POST | `/api/tonight/undo` | `tonight_undo` |
| POST | `/api/tonight/reset` | `tonight_reset` |
| GET | `/api/spoiler-protection` | `spoilers` |
| POST | `/api/spoiler-protection` | `update_spoilers` |
| GET | `/api/series-playback-preference` | `playback` |
| POST | `/api/series-playback-preference` | `update_playback` |

## Hors ligne

`crates/kino/src/routes/offline.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/downloads` | `list` |
| POST | `/api/download` | `create` |
| POST | `/api/download-season` | `season` |
| POST | `/api/download-cancel` | `pause` |
| POST | `/api/download-resume` | `resume` |
| POST | `/api/download-restart` | `restart` |
| POST | `/api/download-remove` | `remove` |
| GET | `/api/offline/{kind}/{id}` | `asset` |
| HEAD | `/api/offline/{kind}/{id}` | `asset` |

## Manga

`crates/kino/src/routes/manga.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/manga/search` | `search` |
| GET | `/api/manga/popular` | `popular` |
| GET | `/api/manga/row` | `row` |
| GET | `/api/manga/details` | `details` |
| GET | `/api/manga/chapters` | `chapters` |
| GET | `/api/manga/pages` | `pages` |
| GET | `/api/manga/volumes` | `volumes` |
| GET | `/api/manga/volume/files` | `volume_files` |
| GET | `/api/manga/volume/status` | `volume_status` |
| POST | `/api/manga/volume` | `volume_start` |
| GET | `/api/manga/local` | `local_series` |
| GET | `/api/manga/local/chapters` | `local_chapters` |
| GET | `/api/manga/local/page` | `local_page` |
| GET | `/api/manga/progress` | `progress` |
| POST | `/api/manga/progress` | `save_progress` |
| POST | `/api/manga/folder` | `folder` |

## P2P

`crates/kino/src/routes/p2p.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/p2p` | `state` |
| POST | `/api/p2p/consent` | `consent` |
| POST | `/api/p2p/settings` | `settings` |
| POST | `/api/p2p/clear` | `clear` |
| GET | `/p2p/{hash}/{index}` | `file` |
| HEAD | `/p2p/{hash}/{index}` | `file` |

## Lire sur la TV

`crates/kino/src/routes/tv.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/tv/devices` | `devices` |
| GET | `/api/tv/status` | `status` |
| POST | `/api/tv/device` | `choose` |
| POST | `/api/tv/play` | `play` |
| POST | `/api/tv/control` | `control` |
| GET | `/tv/stream/{*rest}` | `relay` |
| HEAD | `/tv/stream/{*rest}` | `relay` |

## Télécommande, état du lecteur, diagnostic

`crates/kino/src/routes/remote.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/remote` | `page` |
| GET | `/api/remote/state` | `state` |
| POST | `/api/remote/control` | `control` |
| GET | `/api/remote/info` | `info` |
| GET | `/api/remote/qr` | `info` |
| POST | `/api/player/state` | `player_state` |
| GET | `/api/player/commands` | `commands` |
| GET | `/api/diagnostic` | `diagnostic` |

## Fenêtre et ordinateur

`crates/kino/src/routes/window.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/discord-rpc/status` | `discord_status` |
| POST | `/api/discord-rpc/update` | `discord_update` |
| POST | `/api/discord-rpc/clear` | `discord_clear` |
| POST | `/api/window/action` | `window_action` |
| POST | `/api/open-folder` | `open_folder` |
| POST | `/api/save-screenshot` | `save_screenshot` |
| POST | `/api/probe-media` | `probe_media` |
| POST | `/api/upload-torrent` | `upload_torrent` |
| POST | `/api/notifications/test` | `notifications_test` |
| GET | `/api/remux` | `remux` |

## Paramètres et comptes

`crates/kino/src/routes/settings.rs`

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/config` | `read` |
| POST | `/api/config` | `save` |
| POST | `/api/tmdb/key` | `tmdb_key` |
| POST | `/api/client-error` | `client_error` |
| GET | `/api/stremio-addons` | `stremio_addons` |
| POST | `/api/stremio-addons` | `change_stremio_addons` |
| GET | `/api/stremio-addons/catalog` | `stremio_catalog` |
| GET | `/api/addons` | `addons` |
| GET | `/api/backups` | `backups` |
| GET | `/api/network` | `network` |
| GET | `/api/sync/gdrive` | `gdrive` |
| POST | `/api/sync/gdrive` | `gdrive` |
| POST | `/api/backups/now` | `backup_now` |
| POST | `/api/backups/restore` | `backup_restore` |
| GET | `/api/trakt/status` | `trakt_status` |
| POST | `/api/trakt/auth/start` | `trakt_start` |
| POST | `/api/trakt/auth/poll` | `trakt_poll` |
| POST | `/api/trakt/disconnect` | `trakt_disconnect` |
| POST | `/api/trakt/scrobble` | `trakt_scrobble` |
| POST | `/api/trakt/sync` | `trakt_sync` |
| POST | `/api/addons/toggle` | `toggle_addon` |

## Sans internet

`network.rs` sait si internet répond : vérification au lancement et sur
`/api/network?refresh=1`, puis chaque requête de `net.rs`. Une panne
de réseau (pas de DNS, pas de route) ne relance pas la requête et donne
« Pas de connexion à internet. ». Pendant dix secondes après une telle panne,
les requêtes échouent tout de suite. Toute réponse 500 donnée pendant ce temps
porte `"code": "offline"`, que l'interface reconnaît.

## Diagnostic

`/api/diagnostic` lit l'état de KINO sans rien modifier (`diagnostic.rs`) :
internet, compte Debrid, Trakt, Google Drive, lecteur, notifications, espace
disque, base de données. Chaque ligne a un état (`ok`, `warning`, `problem`,
`off`), une phrase et, si besoin, quoi faire. Le rapport à copier ne contient
ni clé, ni lien, ni adresse e-mail, ni nom de compte.

## Ajouter une route

1. Écrire la fonction dans le fichier du thème (`crates/kino/src/routes/`).
2. L'ajouter au `router()` de ce fichier.
3. Ajouter sa ligne à ce document.
