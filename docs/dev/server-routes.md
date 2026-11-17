# Routes du serveur

Le serveur local de KINO (`app.py`) répond à l'interface. Chaque adresse
exacte a sa fonction, rangée par thème dans une classe ; deux tables
(`RequestHandler.GET_ROUTES` et `POST_ROUTES`) les relient.
`do_GET` et `do_POST` ne gardent que :

- le contrôle d'accès ;
- les adresses qui demandent un motif, un préfixe ou une seconde condition ;
- la réponse « Route introuvable ».

Cas gardés dans `do_GET` / `do_POST` :

- `/api/offline/…` : fichiers, affiches et sous-titres de la bibliothèque hors ligne ;
- `/p2p/<empreinte>/<fichier>` : le fichier d'un torrent lu en P2P, servi au lecteur de ce Mac seulement (`p2p_engine.serve`) ;
- `/`, `/index.html`, `/icons.svg`, `/css/…`, `/js/…` : la page et ses fichiers ;
- `/qrcode.min.js` : passe à la suite s'il manque ;
- `/api/remote/info`, `/api/remote/qr` : appairage de la télécommande ;
- `/api/search?type=all` : la recherche « Tout » ;
- `/api/native-player/open|command|close|screenshot|subtitle` : le lecteur natif ;
- `/api/download-resume|restart|remove` : bibliothèque hors ligne ;
- `/api/mpv`, `/api/vlc` : lecteurs externes ;
- `/tv/stream/<jeton>/…` (GET, HEAD) : le relais de « Lire sur la TV », seulement pour l'adresse de la TV en cours et son jeton (`tv_cast.py`).

## Lecture : sources, débridage, lecteurs, sous-titres, épisode suivant

Classe `PlaybackRoutes`.

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/player/commands` | `get_player_commands` |
| GET | `/api/remux` | `get_remux` |
| GET | `/api/auto-stream` | `get_auto_stream` |
| GET | `/api/series-playback-preference` | `get_series_playback_preference` |
| GET | `/api/intro-times` | `get_intro_times` |
| GET | `/api/subtitles` | `get_subtitles` |
| GET | `/api/subtitle-cues` | `get_subtitle_cues` |
| GET | `/api/native-player/state` | `get_native_player_state` |
| GET | `/api/trailer` | `get_trailer` |
| GET | `/api/torrents` | `get_torrents` |
| POST | `/api/series-playback-preference` | `post_series_playback_preference` |
| POST | `/api/playback-feedback` | `post_playback_feedback` |
| POST | `/api/probe-media` | `post_probe_media` |
| POST | `/api/player/state` | `post_player_state` |
| POST | `/api/save-screenshot` | `post_save_screenshot` |
| POST | `/api/upload-torrent` | `post_upload_torrent` |
| POST | `/api/debrid` | `post_debrid` |
| POST | `/api/one-click-play` | `post_one_click_play` |
| POST | `/api/debrid-status` | `post_debrid_status` |

## Catalogue et recherche : catalogue, recherche, fiches, anime, nouveaux épisodes

Classe `CatalogueRoutes`.

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/catalog` | `get_catalog` |
| GET | `/api/related` | `get_related` |
| GET | `/api/for-you` | `get_for_you` |
| GET | `/api/new-episodes` | `get_new_episodes` |
| GET | `/api/episodes-fr` | `get_episodes_fr` |
| GET | `/api/people` | `get_people` |
| GET | `/api/person` | `get_person` |
| GET | `/api/search-suggest` | `get_search_suggest` |
| GET | `/api/search` | `get_search` |
| GET | `/api/meta` | `get_meta` |
| GET | `/api/series-meta` | `get_series_meta` |
| GET | `/api/anime/trending` | `get_anime_trending` |
| GET | `/api/anime/popular` | `get_anime_popular` |
| GET | `/api/anime/search` | `get_anime_search` |
| GET | `/api/anime/journey` | `get_anime_journey` |
| GET | `/api/anime/filler` | `get_anime_filler` |
| GET | `/api/anime/home` | `get_anime_home` |
| GET | `/api/manga/search` | `get_manga_search` |
| GET | `/api/manga/popular` | `get_manga_popular` |
| GET | `/api/manga/details` | `get_manga_details` |
| GET | `/api/manga/chapters` | `get_manga_chapters` |
| GET | `/api/manga/pages` | `get_manga_pages` |
| GET | `/api/manga/local` | `get_manga_local` |
| GET | `/api/manga/local/chapters` | `get_manga_local_chapters` |
| GET | `/api/manga/local/page` | `get_manga_local_page` |
| GET | `/api/manga/progress` | `get_manga_progress` |
| GET | `/api/anime/week` | `get_anime_week` |
| POST | `/api/anime/open` | `post_anime_open` |
| POST | `/api/anime/journey/resolve` | `post_anime_journey_resolve` |
| POST | `/api/anime/journey/order` | `post_anime_journey_order` |
| POST | `/api/french-titles` | `post_french_titles` |
| POST | `/api/original-titles` | `post_original_titles` |

## Bibliothèque : Ma Liste, historique, hors ligne, statistiques, Ce soir, spoilers

Classe `LibraryRoutes`.

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/my-series` | `get_my_series` |
| GET | `/api/calendar` | `get_calendar` |
| GET | `/api/stats` | `get_stats` |
| GET | `/api/user-lists` | `get_user_lists` |
| GET | `/api/downloads` | `get_downloads` |
| GET | `/api/spoiler-protection` | `get_spoiler_protection` |
| POST | `/api/spoiler-protection` | `post_spoiler_protection` |
| POST | `/api/tonight` | `post_tonight` |
| POST | `/api/tonight/feedback` | `post_tonight_feedback` |
| POST | `/api/tonight/undo` | `post_tonight_undo` |
| POST | `/api/tonight/reset` | `post_tonight_reset` |
| POST | `/api/watched-toggle` | `post_watched_toggle` |
| POST | `/api/watchlist` | `post_watchlist` |
| POST | `/api/history-remove` | `post_history_remove` |
| POST | `/api/history-record` | `post_history_record` |
| POST | `/api/rating` | `post_rating` |
| POST | `/api/download` | `post_download` |
| POST | `/api/download-season` | `post_download_season` |
| POST | `/api/download-cancel` | `post_download_cancel` |

## Cloud Debrid : historique, nettoyage, titres, redirections AllDebrid et TorBox

Classe `DebridRoutes`.

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/ad-dl` | `get_ad_dl` |
| GET | `/api/torbox-dl` | `get_torbox_dl` |
| GET | `/api/rd-history` | `get_rd_history` |
| POST | `/api/rd-delete` | `post_rd_delete` |
| POST | `/api/rd-cleanup` | `post_rd_cleanup` |
| POST | `/api/cloud-titles` | `post_cloud_titles` |

## Listes et communauté : Letterboxd, sélections d'Explorer

Classe `ListsRoutes`.

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/community/curated` | `get_community_curated` |
| GET | `/api/letterboxd/custom-lists` | `get_letterboxd_custom_lists` |
| POST | `/api/import-letterboxd` | `post_import_letterboxd` |
| POST | `/api/letterboxd/import-list` | `post_letterboxd_import_list` |
| POST | `/api/letterboxd/delete-list` | `post_letterboxd_delete_list` |

## Réglages et comptes : configuration, Trakt, Discord, Google Drive, add-ons, notifications

Classe `AccountRoutes`.

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/api/config` | `get_config` |
| POST | `/api/tmdb/key` | `post_tmdb_key` |
| GET | `/api/for-you/status` | `get_for_you_status` |
| POST | `/api/for-you/not-for-me` | `post_not_for_me` |
| POST | `/api/for-you/reset` | `post_for_you_reset` |
| GET | `/api/trakt/status` | `get_trakt_status` |
| GET | `/api/sync/gdrive` | `get_sync_gdrive` |
| GET | `/api/discord-rpc/status` | `get_discord_rpc_status` |
| GET | `/api/addons` | `get_addons` |
| GET | `/api/p2p` | `get_p2p` |
| GET | `/api/stremio-addons` | `get_stremio_addons` |
| GET | `/api/stremio-addons/catalog` | `get_stremio_catalog` |
| POST | `/api/config` | `post_config` |
| POST | `/api/sync/gdrive` | `post_sync_gdrive` |
| POST | `/api/trakt/auth/start` | `post_trakt_auth_start` |
| POST | `/api/trakt/auth/poll` | `post_trakt_auth_poll` |
| POST | `/api/trakt/disconnect` | `post_trakt_disconnect` |
| POST | `/api/trakt/scrobble` | `post_trakt_scrobble` |
| POST | `/api/trakt/sync` | `post_trakt_sync` |
| POST | `/api/discord-rpc/update` | `post_discord_rpc_update` |
| POST | `/api/discord-rpc/clear` | `post_discord_rpc_clear` |
| POST | `/api/addons/toggle` | `post_addons_toggle` |
| POST | `/api/p2p/consent` | `post_p2p_consent` |
| POST | `/api/p2p/settings` | `post_p2p_settings` |
| POST | `/api/p2p/clear` | `post_p2p_clear` |
| POST | `/api/manga/progress` | `post_manga_progress` |
| POST | `/api/manga/folder` | `post_manga_folder` |
| POST | `/api/stremio-addons` | `post_stremio_addons` |
| POST | `/api/notifications/test` | `post_notifications_test` |

## Application : télécommande, fenêtre, dossier de téléchargement, connexion, diagnostic, TV

Classe `AppRoutes`.

| Méthode | Adresse | Fonction |
|---|---|---|
| GET | `/remote` | `get_remote` |
| GET | `/api/remote/state` | `get_remote_state` |
| GET | `/api/network` | `get_network` |
| GET | `/api/diagnostic` | `get_diagnostic` |
| POST | `/api/remote/control` | `post_remote_control` |
| GET | `/api/tv/devices` | `get_tv_devices` |
| GET | `/api/tv/status` | `get_tv_status` |
| POST | `/api/client-error` | `post_client_error` |
| POST | `/api/tv/device` | `post_tv_device` |
| POST | `/api/tv/play` | `post_tv_play` |
| POST | `/api/tv/control` | `post_tv_control` |
| GET | `/api/backups` | `get_backups` |
| POST | `/api/backups/now` | `post_backup_now` |
| POST | `/api/backups/restore` | `post_backup_restore` |
| POST | `/api/window/action` | `post_window_action` |
| POST | `/api/open-folder` | `post_open_folder` |

## Sans internet

`network_state.py` sait si internet répond : vérification au lancement et sur
`/api/network?refresh=1`, puis chaque requête de `config.http_json`. Une panne
de réseau (pas de DNS, pas de route) ne relance pas la requête et donne
« Pas de connexion à internet. ». Pendant dix secondes après une telle panne,
les requêtes échouent tout de suite. Toute réponse 500 donnée pendant ce temps
porte `"code": "offline"`, que l'interface reconnaît.

## Diagnostic

`/api/diagnostic` lit l'état de KINO sans rien modifier (`diagnostic.py`) :
internet, compte Debrid, Trakt, Google Drive, lecteur, notifications, espace
disque, base de données. Chaque ligne a un état (`ok`, `warning`, `problem`,
`off`), une phrase et, si besoin, quoi faire. Le rapport à copier ne contient
ni clé, ni lien, ni adresse e-mail, ni nom de compte.

## Ajouter une route

1. Écrire la fonction dans la classe du thème : `get_…(self, parsed, params)` ou
   `post_…(self, parsed, params, body)`.
2. L'ajouter à `GET_ROUTES` ou `POST_ROUTES`.
3. Régénérer ce tableau. `test_server_routes.py` vérifie que chaque adresse a sa fonction et
   que ce document les liste toutes.
