# KINO

KINO est une application de bureau pour macOS qui réunit au même endroit le
catalogue de films, de séries et d'animes, le suivi de ce que l'on regarde et
la lecture, à partir de votre propre compte chez un service de débridage
(Real-Debrid, AllDebrid, TorBox, Debrid-Link, Premiumize).

L'interface est en français.

## Avertissement

KINO n'héberge, ne fournit et ne distribue aucun contenu. Il interroge des
sources que l'utilisateur choisit et lit ce que son propre service de
débridage lui donne. Chaque utilisateur est seul responsable de ce qu'il
regarde et du respect du droit d'auteur dans son pays.

## Ce que fait KINO

- **Catalogue** : films, séries et animes, avec fiches, distribution,
  bandes-annonces et titres en français.
- **Lecture** : lecteur intégré basé sur mpv (HDR, sous-titres, pistes audio,
  saut du générique), ou mpv, IINA et VLC en lecteur externe.
- **Suivi** : Ma Liste, lectures en cours, Déjà vus, Mes séries, calendrier
  des sorties, notes en un geste.
- **Suggestions** : « Pour vous » et « Si vous avez aimé », calculées sur ce
  Mac à partir de ce que vous avez vu et noté.
- **Anime** : rangées tirées d'AniList, planning de la semaine, parcours d'une
  saga dans l'ordre de sortie.
- **Liens avec d'autres services** : import Letterboxd, Trakt, présence
  Discord.
- **À plusieurs et ailleurs** : Watch Party, lecture sur une TV du réseau
  local, télécommande depuis un téléphone.
- **Hors ligne** : téléchargement d'un film ou d'une saison pour la regarder
  sans internet.
- **Vos données** : rangées sur ce Mac, avec une copie de sauvegarde par jour
  et une synchronisation possible par Google Drive.

## Prérequis

- Un Mac Apple Silicon (arm64) sous macOS récent.
- Python 3.12.
- [IINA](https://iina.io), installé dans `/Applications` : KINO en reprend la
  bibliothèque libmpv pour son lecteur intégré.
- Un compte chez un service de débridage pris en charge, dont la clé se colle
  dans Paramètres.

## Lancer KINO depuis le code

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python tools/vendor_libmpv.py
.venv/bin/python desktop.py
```

`tools/vendor_libmpv.py` copie libmpv et ses dépendances depuis IINA dans
`vendor/libmpv`. Sans cette étape, KINO fonctionne avec un lecteur externe.

## Construire l'application

```bash
.venv/bin/pyinstaller --noconfirm kino_macos.spec
```

L'application se trouve ensuite dans `dist/KINO.app`. Elle vérifie ses propres
dépendances avec `KINO.app/Contents/MacOS/KINO --self-check`.

## Où sont les données

- `~/.kino_data.db` : bibliothèque, historique, notes et cache.
- `~/.rd_cinehub_config.json` : réglages et clés des services.
- `~/Library/Application Support/KINO` : sauvegardes, journal d'erreurs,
  mises à jour.

Les clés restent sur ce Mac : elles ne sont ni sauvegardées ni synchronisées.

## Organisation du code

| Emplacement | Rôle |
|---|---|
| `desktop.py` | Fenêtre de l'application (pywebview), démarrage |
| `app.py` | Serveur local qui sert l'interface et répond à ses requêtes |
| `kino_db.py`, `config.py` | Base de données et réglages |
| `meta_engine.py`, `tmdb.py`, `anime_*.py` | Fiches, titres en français, animes |
| `debrid_engine.py`, `torrent_engine.py`, `stream_engine.py` | Sources et services de débridage |
| `native_player.py`, `native_view.py`, `player_engine.py` | Lecteur intégré et lecteurs externes |
| `recommender.py`, `ratings.py` | Suggestions et notes |
| `addons/` | Sources supplémentaires, activables dans Paramètres |
| `web/` | Interface (HTML, CSS, JavaScript) |

## Windows

Le code prévoit Windows (fenêtre Edge WebView2), mais le portage n'est pas
terminé : le lecteur intégré, les mises à jour et les notifications n'existent
encore que sur macOS.

## Licence

KINO est publié sous licence MIT (voir [LICENSE](LICENSE)). Le dossier
`web/js/vendor` contient PeerJS, sous sa propre licence MIT. libmpv et FFmpeg,
copiés au moment de la construction, sont sous licence GPL ou LGPL.
