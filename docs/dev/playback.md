# Lecture : de « Lancer » au lecteur

Ce document suit une lecture pas à pas. Chaque étape est une fonction courte
et testée (`test_playback_steps.py`). Les réponses sont fixées par
`test_playback_pins.py` : mêmes scores, même ordre de sources, mêmes réponses.

## « Lecture en un clic » (`POST /api/one-click-play`)

`post_one_click_play` dans `app.py` enchaîne :

1. **La clé** : sans clé Debrid, réponse 401 `one_click.NO_PROVIDER`.
2. **La demande** : `one_click.request(body, cfg)` lit le titre, le type
   (film, série, anime), la saison, l'épisode et le lecteur demandé, avec les
   valeurs par défaut de KINO.
3. **La durée du film** : `get_media_meta`, puis `one_click.runtime_minutes`.
   Elle aide la recherche à écarter les homonymes.
4. **Les sources** : `get_available_streams(query)` (`stream_engine`), déjà
   classées.
5. **Les sources écartées** :
   - `one_click.set_aside(body)` donne les clés que la page demande de passer
     (au plus 30, bien formées) ;
   - `playback_support.excluded` ajoute celles qui ont déjà échoué pour ce
     titre.
6. **Les essais** : `stream_engine.try_sources(...)`, détaillé plus bas, avec
   `one_click.file_aliases` pour reconnaître le fichier d'un film.
7. **Le départ** : `start_one_click(play, source, réponse Debrid, fichier)`.
   - Pour une série, la liste de lecture (`build_series_playlist_items`) et
     la préparation de l'épisode suivant (`prefetch_next_episode`).
   - Le point de reprise (`get_resume_position`).
   - `one_click.media_context` : ce que le lecteur et l'historique savent du
     titre.
   - Le lecteur : `one_click.uses_built_in_player` choisit celui de KINO,
     sauf si mpv est demandé ; sur Mac, sauf si « external » l'est. Avec mpv,
     `launch_mpv`, puis `record_history`.
8. **La réponse** : la source choisie, les clés essayées et le fichier.
   - Sans source prête : 422 `one_click.NO_SOURCE`.
   - Si le fournisseur refuse : 401 `one_click.REFUSED`.

## Essayer les sources : `stream_engine.try_sources`

Elle sert à « Lecture en un clic » et à l'épisode suivant. Elle parcourt les
dix premières sources dans leur ordre :

- Une source écartée est passée, mais elle compte dans les dix.
- Pour chaque autre source, elle demande le lien Debrid, cherche le bon
  fichier (`select_playable_file`), puis appelle `use(...)`.
- Une source pas prête, sans bon fichier, ou qui échoue passe à la suivante,
  y compris quand le lecteur ne démarre pas. La dernière erreur est gardée.
- Seule une erreur pour laquelle `stop(erreur)` est vrai arrête tout.
  « Lecture en un clic » s'arrête ainsi sur un refus du fournisseur : un code
  HTTP 401 ou 403 porté par l'erreur.
- Le résultat est un `SourceAttempts` : found, result, key, tried, last_error.

`stream_candidate_key` donne la clé opaque d'une source. `app.stream_candidate_key`
reste le même objet.

## L'épisode suivant : `resolve_auto_stream_episode` (`player_engine.py`)

1. Un lien trouvé dans les 30 dernières minutes est réutilisé.
2. Sinon, les sources de l'épisode : `_prefer_previous_source` garde l'ordre
   du serveur, et la source de l'épisode précédent ne gagne qu'une égalité.
3. `stream_engine.try_sources`, puis le lien est gardé et l'historique mis à
   jour (`_episode_entry`). Ce n'est pas le cas pour un téléchargement hors
   ligne (`update_history=False`).
4. Sans résultat : « Impossible de résoudre S02E05 (dernière erreur) ».

## La recherche de sources : `search_multi_torrents` (`torrent_engine.py`)

1. `stream_target` : `movie/tt…` ou `series/tt…:saison:épisode`.
2. `indexer_sources` donne, dans l'ordre de lancement :
   - Torrentio (20 s), Torrentio FR (10 s), Torrentio Anime (10 s) ;
   - Comet (`comet_url`, avec le service Debrid quand une clé existe, 10 s) ;
   - KnightCrawler (4 s), TPB+ (6 s), Peerflix (6 s).
3. `fetch_streams` interroge tout en même temps, avec APIBay et les modules.
   - L'attente est de 20 s au plus pour Torrentio, 8 s pour ses listes
     française et anime, 4 s pour les autres. Une réponse plus tardive est
     laissée de côté.
   - Un indexeur en panne laisse sa liste vide.
4. `merge_streams` garde une entrée par empreinte (info_hash), en commençant
   par les listes françaises (`MERGE_ORDER`). Un doublon ajoute ses langues,
   ainsi que sa disponibilité immédiate, son lien et ses qualités.
5. `keep_relevant_streams` écarte les faux, les CAM, les autres œuvres et les
   autres épisodes, puis explique chaque source gardée (`recommendation`).
6. `rank_streams` note chaque source (`score_torrent`) et trie : par score
   (« Recommandé »), par sources, par taille ou par qualité.

## Le score : `score_torrent` = somme de onze parties

`score_breakdown` donne le détail ; `SCORE_PARTS` en garde l'ordre.

| Partie | Question | Points |
|---|---|---|
| `_instant_points` | Déjà en cache Debrid ? | +3500 |
| `_quality_points` | Résolution et source face à la qualité voulue | 0 à +480 |
| `_language_points` | Langue face à la langue voulue | +50 à +950 |
| `_picture_points` | HDR, Dolby Vision, SDR face au réglage d'image | Image SDR : SDR +40, HDR et DV + HDR 0, DV seul −350 ; HDR : jusqu'à +110 |
| `_sound_points` | Atmos, TrueHD/DTS-HD, DD+/5.1 | 0 à +80 |
| `_seeder_points` | Santé du torrent | 0 à +80 |
| `_fake_penalty` | Taille impossible, CAM/TS | −10000, −20000 |
| `_size_points` | Taille adaptée à la qualité | −80 à +60 |
| `_anime_group_points` | Équipe anime connue, Nyaa | 0, +260, +340 |
| `_year_points` | Année du titre confirmée, ou autre œuvre | +500, −10000 |
| `_priority_points` | « La qualité choisie d'abord » (réglage Priorité) | 0 ou +10000 |

## Le réglage « Priorité » (`source_priority`)

- **« Lancer vite »** (`speed`, par défaut) : l'ordre est le même qu'avant ce
  réglage. Une source prête tout de suite passe avant le reste (+3500).
- **« La qualité choisie d'abord »** (`quality`) : `_priority_points` ajoute
  +10000 aux sources qui réunissent toutes ces conditions :
  - la qualité choisie est annoncée (4K, ou 1080p si c'est le réglage) ;
  - elles sont dans une langue acceptée (au moins 400 points de langue en VF,
    500 en VOSTFR) ;
  - elles ne sont ni en Dolby Vision seul, ni des faux, ni d'une autre année.

  Ces sources passent donc devant toutes les autres, prêtes ou non. Entre
  elles, l'ordre habituel reste : prête d'abord, puis langue, image, son…
  Dans la fiche, « Lancer » sur la recommandée attend que Debrid la prépare.
- « Lecture en un clic », en « qualité d'abord » (`one_click_waiting`) :
  - si une source dans la qualité choisie est prête, elle se lance ;
  - sinon, KINO commence à préparer la meilleure de cette qualité
    (`one_click.source_to_prepare`). Si Debrid l'a en quelques secondes, elle
    se lance ; sinon la réponse vaut 202 `preparing`, avec la progression et
    `ready_alternative`.
  - La page affiche « Préparation de la qualité choisie : x % » et interroge
    `/api/debrid-status` toutes les 3 s, jamais fenêtre cachée.
  - Quand c'est prêt, elle rappelle `/api/one-click-play` avec
    `prepared: {torrent_id, source_key}`. Le torrent préparé est alors lu
    directement, sans nouvelle demande à Debrid.
  - Deux boutons : « Lancer une source prête à la place » (`prefer_ready`,
    les sources prêtes d'abord) et « Arrêter d'attendre » (la préparation
    continue sur Debrid).
- La reprise après un échec de lecture (`prefer_ready`) et l'épisode suivant
  n'attendent jamais. Ils essaient d'abord les sources prêtes
  (`one_click.ready_first`).
- Le réglage fait partie de la clé de cache de la recherche : changer de
  réglage reclasse les sources.

## « Pourquoi cette source » dans la fiche

`rank_streams` calcule les onze parties une seule fois par source. Le score
reste leur somme, sans changement. Chaque source reçoit aussi :

- `score_parts` : les points de chaque partie ;
- `why` : les raisons en mots (`score_reasons`), au format `{part, text, tone,
  points}`. Le ton vaut « good », « neutral » ou « bad ». Une partie qui ne dit
  rien d'utile, comme peu de pairs ou aucun format de son, ne donne pas de
  raison. Une qualité non annoncée reste « Qualité non indiquée », même si
  elle est comptée comme du 1080p.

Dans la fiche (`app-sources.js`) :

- **La source recommandée** affiche « Pourquoi celle-ci : » suivi de ses
  quatre meilleures raisons, de la plus forte à la plus faible.
- **Chaque source** a un repli « Pourquoi ce rang ? » avec :
  - les points forts ;
  - « À noter » : ce qui est moins bon, puis ce qui est neutre ;
  - « Moins bien que la recommandée : » les trois plus grands écarts, et ce
    qu'elle fait mieux (`sourceComparison`).
- Une réponse plus ancienne, sans `why`, garde l'ancienne ligne « Pourquoi : »,
  faite des signaux annoncés.
- Un épisode masqué contre les spoilers n'affiche aucune raison.

## Rendu HDR sur un écran standard

Le lecteur natif (libmpv, sortie OpenGL) ne fixe aucun réglage d'image : il
laisse mpv convertir le HDR selon ses réglages par défaut (`tone-mapping=auto`,
`target-trc=auto`). `test_native_hdr.py` empêche qu'un réglage s'y ajoute sans
nouvelle mesure.

Mesure faite dans le lecteur natif, avec un fichier HDR10 d'essai : HEVC
Main 10, PQ, BT.2020, max-cll 1000, et cinq gris de luminance connue. Valeurs
lues à l'écran, sur 255 :

| Gris (HDR10) | 10 nits | 100 nits | 203 nits | 1000 nits | 4000 nits |
|---|---|---|---|---|---|
| Lecteur natif tel quel | 66 | 182 | 225 | 255 | 254 |
| Avec les réglages du lecteur externe (`bt.2446a`, pic mesuré) | 66 | 182 | 225 | 255 | 254 |
| Mêmes valeurs non converties (SDR) | 76 | 127 | 145 | 189 | 227 |

- **Lecture du tableau** : le HDR10 est bien converti. Un gris de 100 nits
  sort à 182, sa luminosité SDR exacte, alors qu'une image non convertie le
  montrerait à 127, terne. Les hautes lumières se replient sur le blanc.
- **Réglages du lecteur externe** : les ajouter ne change rien de mesurable.
- **Dolby Vision** : les versions « DV + HDR » portent une base HDR10, lue de
  la même façon. Le Dolby Vision seul, sans base HDR10, demande un rendu que
  cette sortie n'a pas, et ses couleurs peuvent être fausses : le score le
  met après, et « La qualité choisie d'abord » l'écarte.
- La raison affichée pour une source HDR est donc « HDR, converti pour votre
  écran standard », sur un ton neutre.
- Avec « Image : SDR », le HDR n'est pas pénalisé. Une source SDR garde +40, pour
  départager deux sources égales ; le Dolby Vision seul garde −350. Le réglage
  « HDR et Dolby Vision » est inchangé.

## Le lien Debrid d'un magnet : `rd_debrid_magnet` (`debrid_engine.py`)

1. Sans clé : « Clé API … manquante ».
2. Un lien de résolution (Torrentio, Comet), ou un magnet qui est un lien
   http, est essayé d'abord (`resolve_torrentio_rd_url`). En cas d'échec, un
   vrai magnet continue ; un lien http s'arrête.
3. `MAGNET_SERVICES` choisit la fonction du service du compte ; tout autre
   service est Real-Debrid :

   | Service | Fonction | Ce qu'elle fait |
   |---|---|---|
   | Real-Debrid | `_realdebrid_magnet` | ajoute, choisit les vidéos de plus de 25 Mo (`_realdebrid_select_videos`), attend jusqu'à 8 s (`_realdebrid_wait`), puis débride |
   | AllDebrid | `_alldebrid_magnet` | envoie, vérifie jusqu'à 6 fois, puis débloque |
   | Debrid-Link | `_debridlink_magnet` | la seedbox liste les fichiers déjà prêts |
   | Premiumize | `_premiumize_magnet` | seulement ce qui est déjà en cache |
   | TorBox | `_torbox_magnet` | ajoute, puis lit le torrent (`rd_check_torrent`) |
   | Mega-Debrid | `_megadebrid_magnet` | liens d'hébergeurs seulement |

4. Les réponses ont une seule forme :
   - `_ready_files` : prêt, l'épisode voulu d'abord ;
   - `_not_ready` : en cours, avec la progression, la vitesse et les pairs ;
   - `_file_entry` : un fichier.

## Le lecteur externe : `launch_mpv` (`player_engine.py`)

1. `_claim_mpv_launch` : un second lancement dans les secondes qui suivent
   reçoit la réponse du premier. Sans lecteur trouvé : erreur.
2. `_playback_target` : le fichier, ou une liste M3U s'il y a plusieurs
   épisodes. `_write_mpv_input_conf` ajoute la touche « s » (+85 s).
3. La commande, selon le lecteur :
   - `_iina_command` : IINA par `iina-cli`, options mpv en « --mpv- » ;
   - VLC : le média seul ;
   - `_mpv_command` : sans bordure, titré KINO. Sur Windows, la fenêtre est
     ancrée dans KINO ou placée à son endroit (`_mpv_window_options`).
   - Son, image HDR et reprise sont communs à mpv et à IINA :
     `_shared_mpv_options`.
4. La fenêtre KINO se cache, et Discord affiche le titre (`_show_in_discord`).
5. Le départ : `_start_on_windows` (un seul lecteur, ancré ou sur le bureau)
   ou `_start_elsewhere` (session à part, suivie jusqu'à sa fermeture).
   `_remember_mpv_launch` garde la réponse.

Ces deux fonctions sont fixées par `test_debrid_mpv_pins.py`.

