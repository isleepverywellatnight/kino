# KINO

KINO est une application de bureau pour macOS et Windows qui réunit au même endroit le
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
- **Suggestions** : « Pour vous » et « Si vous avez aimé », calculées sur cet
  ordinateur à partir de ce que vous avez vu et noté.
- **Anime** : rangées tirées d'AniList, planning de la semaine, parcours d'une
  saga dans l'ordre de sortie.
- **Liens avec d'autres services** : import Letterboxd, Trakt, présence
  Discord.
- **À plusieurs et ailleurs** : Watch Party, lecture sur une TV du réseau
  local, télécommande depuis un téléphone.
- **Hors ligne** : téléchargement d'un film ou d'une saison pour la regarder
  sans internet.
- **Vos données** : rangées sur cet ordinateur, avec une copie de sauvegarde par jour
  et une synchronisation possible par Google Drive.

## Installer KINO

Téléchargez la dernière version sur la page
[Releases](https://github.com/isleepverywellatnight/kino/releases/latest) :

- **macOS** (Apple Silicon, macOS 14 ou plus récent) : `KINO-x.y.z-arm64.dmg`.
  Ouvrez-le et glissez KINO dans Applications. KINO n'est pas encore signé par
  Apple : au premier lancement, allez dans Réglages Système → Confidentialité
  et sécurité, puis cliquez sur « Ouvrir quand même ».
- **Windows** (10 ou 11, 64 bits) : `KINO-Setup-x.y.z-x64.exe`. L'installation
  se fait pour l'utilisateur courant, sans droits administrateur. Si Windows
  affiche « Windows a protégé votre ordinateur », cliquez sur « Informations
  complémentaires » puis « Exécuter quand même ».

Le lecteur vidéo et FFmpeg sont inclus : rien d'autre à installer. Il faut
ensuite un compte chez un service de débridage pris en charge, dont la clé se
colle dans Paramètres. Les versions suivantes s'installent depuis KINO :
Paramètres → Mises à jour.

## Lancer KINO depuis le code

Python 3.12 est nécessaire.

macOS (Apple Silicon) : [IINA](https://iina.io), installé dans
`/Applications`, fournit la bibliothèque libmpv du lecteur intégré.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python tools/vendor_libmpv.py
.venv/bin/python desktop.py
```

`tools/vendor_libmpv.py` copie libmpv et ses dépendances depuis IINA dans
`vendor/libmpv`. Sans cette étape, KINO fonctionne avec un lecteur externe.

Windows : [7-Zip](https://7-zip.org) est nécessaire pour extraire mpv.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python tools\fetch_mpv_windows.py
.venv\Scripts\python desktop.py
```

`tools/fetch_mpv_windows.py` télécharge une version précise de mpv, vérifie
son empreinte et la place dans `vendor/mpv-windows`.

## Construire l'application

La CI (`.github/workflows/build.yml`) construit les deux versions à chaque
push. À la main :

```bash
# macOS : dist/KINO.app puis dist/KINO-x.y.z-arm64.dmg
.venv/bin/pyinstaller --noconfirm kino_macos.spec
tools/make_dmg.sh
```

```powershell
# Windows : dist\KINO\KINO.exe puis dist\KINO-Setup-x.y.z-x64.exe (Inno Setup 6)
.venv\Scripts\pyinstaller --noconfirm kino_windows.spec
iscc /DAppVersion=x.y.z installer\kino.iss
```

L'application vérifie ses propres dépendances avec `--self-check`.

## Publier une version

1. Mettez `APP_VERSION` à jour dans `kino_version.py`, puis committez.
2. Créez le tag correspondant et poussez-le :
   `git tag v1.2.0 && git push origin v1.2.0`.
3. La CI construit, teste puis publie la release avec le `.dmg`, l'installateur
   Windows et `SHA256SUMS.txt`.

Pour que KINO puisse installer cette version tout seul, la CI doit signer les
mises à jour. Ajoutez une fois le secret `KINO_UPDATE_SIGNING_KEY` au dépôt
(Settings → Secrets and variables → Actions) avec le contenu de la clé privée
créée par `build_update_package.py --init-key`
(`~/Library/Application Support/KINO/UpdateSigning/private-key.pem`). Sa partie
publique est `update_public_key.hex`. Sans ce secret, la release est publiée
quand même, mais KINO se contente d'annoncer la nouvelle version.

## Où sont les données

- `~/.kino_data.db` : bibliothèque, historique, notes et cache.
- `~/.rd_cinehub_config.json` : réglages et clés des services.
- `~/Library/Application Support/KINO` (macOS) ou `%APPDATA%\KINO` (Windows) :
  sauvegardes, journal d'erreurs. Les mises à jour en cours sont dans
  `%LOCALAPPDATA%\KINO\Updates` sur Windows.

Les clés restent sur cet ordinateur : elles ne sont ni sauvegardées ni
synchronisées. Désinstaller KINO ne supprime pas ces données.

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

La fenêtre utilise Edge WebView2 (l'installateur l'ajoute s'il manque). Le
lecteur est mpv, inclus dans KINO et ancré dans sa fenêtre. Sont encore propres à
macOS : le lecteur intégré sous l'interface (libmpv), les notifications et le
retour à la version précédente.

## Licence

KINO est publié sous licence MIT (voir [LICENSE](LICENSE)). Le dossier
`web/js/vendor` contient PeerJS, sous sa propre licence MIT. libmpv et FFmpeg,
copiés au moment de la construction, sont sous licence GPL ou LGPL.
