# Lecture : de « Lancer » au lecteur

Ce document suit une lecture pas à pas, dans le code Rust (`crates/kino`).
Les réponses sont fixées par les tests de `crates/kino/tests/` (`play.rs`,
`sources.rs`, `resolve.rs`, `player.rs`) et par les cas enregistrés de
l'ancienne version (`crates/kino/tests/fixtures/`) : mêmes scores, même ordre
de sources, mêmes réponses.

## « Lecture en un clic » (`POST /api/one-click-play`)

`one_click_play` (`crates/kino/src/routes/play.rs`) enchaîne :

1. **La clé** : sans clé Debrid, les torrents se lisent en P2P (`p2p.rs`), une
   fois la lecture P2P acceptée ; sinon 409 `p2p_consent`, sauf si une source
   est un simple lien web (`direct_link`).
2. **La demande** : `Play::new(body, cfg)` lit le titre, le type (film, série,
   anime), la saison, l'épisode et le lecteur demandé, avec les valeurs par
   défaut de KINO.
3. **La durée du film** : la fiche Cinemeta, pour écarter les homonymes.
4. **Les sources** : `Search::streams(query, …)` (`sources/search.rs`), déjà
   classées ; « La qualité choisie d'abord » met les sources prêtes devant.
5. **Les sources écartées** : `play::set_aside(body)` (celles que la page
   demande de passer) et `play::excluded` (celles qui ont déjà échoué pour ce
   titre).
6. **Les essais** : `play::try_sources(...)`, détaillé plus bas, avec
   `play::file_aliases` pour reconnaître le fichier d'un film.
7. **Le départ** : `start(...)`.
   - Pour une série, la liste de lecture (`play::season_playlist`) et la
     préparation de l'épisode suivant (`play::prefetch_next`).
   - Le point de reprise (`play::resume_position`).
   - Le lecteur : celui de KINO, sauf si mpv est demandé ; sur Mac, sauf si
     « external » l'est. Avec mpv, `Player::launch` (`player.rs`).
8. **La réponse** : la source choisie, les clés essayées et le fichier.
   - Sans source prête : 422 `no_source`.
   - Si le fournisseur refuse : 401 `provider_auth`.

## Essayer les sources : `play::try_sources`

Elle sert à « Lecture en un clic » et à l'épisode suivant. Elle parcourt les
dix premières sources dans leur ordre :

- Une source écartée est passée, mais elle compte dans les dix.
- Pour chaque autre source, elle demande le lien à son lecteur (`Reader` :
  le service Debrid, ou KINO en P2P), puis cherche le bon fichier
  (`play::select_file`).
- Une source pas prête, sans bon fichier, ou qui échoue passe à la suivante,
  y compris quand le lecteur ne démarre pas. La dernière erreur est gardée.
- Le résultat est un `Attempts` : found, pending, tried, last_error.

`play::candidate_key` donne la clé opaque d'une source.

## L'épisode suivant : `play::resolve_episode` (`crates/kino/src/play.rs`)

1. Un lien trouvé dans les 30 dernières minutes est réutilisé.
2. Sinon, les sources de l'épisode : la source de l'épisode précédent garde l'ordre
   du serveur, et la source de l'épisode précédent ne gagne qu'une égalité.
3. `play::try_sources`, puis le lien est gardé (`EpisodeLinks`) et
   l'historique mis à jour. Ce n'est pas le cas pour un téléchargement hors
   ligne (`update_history` faux).
4. Sans résultat : « Impossible de résoudre S02E05 (dernière erreur) ».

## La recherche de sources : `Search::streams` (`crates/kino/src/sources/search.rs`)

1. `stream_target` : `movie/tt…` ou `series/tt…:saison:épisode`.
2. `indexer_sources` donne, dans l'ordre de lancement :
   - Torrentio (20 s), Torrentio FR (10 s), Torrentio Anime (10 s) ;
   - Comet (`comet_url`, avec le service Debrid quand une clé existe, 10 s) ;
   - KnightCrawler (4 s), TPB+ (6 s), Peerflix (6 s).
3. `fetch_streams` interroge tout en même temps, avec APIBay et Nyaa.
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
| `instant_points` | Déjà en cache Debrid ? | +3500 |
| `quality_points` | Résolution et source face à la qualité voulue | 0 à +480 |
| `language_points` | Langue face à la langue voulue | +50 à +950 |
| `picture_points` | HDR, Dolby Vision, SDR face au réglage d'image | Image SDR : SDR +40, HDR et DV + HDR 0, DV seul −350 ; HDR : jusqu'à +110 |
| `sound_points` | Atmos, TrueHD/DTS-HD, DD+/5.1 | 0 à +80 |
| `seeder_points` | Santé du torrent | 0 à +80 |
| `fake_penalty` | Taille impossible, CAM/TS | −10000, −20000 |
| `size_points` | Taille adaptée à la qualité | −80 à +60 |
| `anime_group_points` | Équipe anime connue, Nyaa | 0, +260, +340 |
| `year_points` | Année du titre confirmée, ou autre œuvre | +500, −10000 |
| `priority_points` | « La qualité choisie d'abord » (réglage Priorité) | 0 ou +10000 |

## Le réglage « Priorité » (`source_priority`)

- **« Lancer vite »** (`speed`, par défaut) : l'ordre est le même qu'avant ce
  réglage. Une source prête tout de suite passe avant le reste (+3500).
- **« La qualité choisie d'abord »** (`quality`) : `priority_points` ajoute
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
`target-trc=auto`). Les options du lecteur natif (`crates/kino/src/native.rs`)
n'en ajoutent aucun : un réglage n'y entre qu'avec une nouvelle mesure.

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

## Le lien Debrid d'un magnet : `Resolver::magnet` (`crates/kino/src/resolve.rs`)

1. Sans clé : « Clé API … manquante ».
2. Un lien de résolution (Torrentio, Comet), ou un magnet qui est un lien
   http, est essayé d'abord (`resolve_link`). En cas d'échec, un
   vrai magnet continue ; un lien http s'arrête.
3. Le service du compte choisit sa fonction ; tout autre service est
   Real-Debrid :

 | Service | Fonction | Ce qu'elle fait |
 |---|---|---|
 | Real-Debrid | `realdebrid` | ajoute, choisit les vidéos de plus de 25 Mo , attend jusqu'à 8 s , puis débride |
 | AllDebrid | `alldebrid` | envoie, vérifie jusqu'à 6 fois, puis débloque |
 | Debrid-Link | `debridlink` | la seedbox liste les fichiers déjà prêts |
 | Premiumize | `premiumize` | seulement ce qui est déjà en cache |
 | TorBox | `torbox` | ajoute, puis lit le torrent (`check`) |
 | Mega-Debrid | `megadebrid` | liens d'hébergeurs seulement |

4. Les réponses ont une seule forme :
   - `ready` : prêt, l'épisode voulu d'abord ;
   - `not_ready` : en cours, avec la progression, la vitesse et les pairs ;
   - `file_entry` : un fichier.

## Le lecteur externe : `Player::launch` (`crates/kino/src/player.rs`)

1. Un second lancement dans les secondes qui suivent reçoit la réponse du
   premier. Sans lecteur trouvé (`player::find`) : erreur.
2. `playback_target` : le fichier, ou une liste M3U s'il y a plusieurs
   épisodes. `input_conf` ajoute la touche « s » (+85 s).
3. La commande, selon le lecteur :
   - `iina_command` : IINA par `iina-cli`, options mpv en « --mpv- » ;
   - VLC : le média seul ;
   - `mpv_command` : sans bordure, titré KINO, placé à l'endroit de la
     fenêtre. Sur Windows, mpv est ancré dans la fenêtre KINO
     (`crates/kino-desktop/src/win_dock.rs`).
   - Son, image HDR et reprise sont communs à mpv et à IINA :
     `shared_options`.
4. La fenêtre KINO se cache (sauf mpv ancré), et Discord affiche le titre.
5. Le départ : `start` (un seul lecteur sur Windows), suivi toutes les 3 s
   jusqu'à sa fermeture (`follow` : historique, épisode de la liste, Discord).

Ces deux parties sont fixées par `crates/kino/tests/resolve.rs` et
`crates/kino/tests/player.rs`.

