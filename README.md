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
- **Lecture** : lecteur vidéo inclus, basé sur mpv (HDR, sous-titres, pistes
  audio, saut du générique).
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

## Télécharger KINO

| Système | Télécharger |
|---|---|
| **macOS** — Mac Apple Silicon (M1 ou plus récent), macOS 14 ou plus récent | [**KINO-macOS.dmg**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-macOS.dmg) |
| **Windows** — Windows 10 ou 11, 64 bits | [**KINO-Windows.exe**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows.exe) |

KINO contient tout ce qu'il lui faut : lecteur vidéo, FFmpeg, interface. Il
n'y a rien d'autre à installer. Il suffit ensuite de coller la clé de votre
service de débridage dans Paramètres.

- **macOS** : ouvrez le fichier `.dmg` et glissez KINO dans Applications.
- **Windows** : lancez `KINO-Windows.exe`. KINO s'installe en un clic, sans
  droits administrateur, puis s'ouvre.

Les versions suivantes s'installent depuis KINO : **Paramètres → Mises à jour**.

### Installer avec une seule commande

Cette commande télécharge la dernière version, vérifie le fichier, installe
KINO et l'ouvre. Elle sert aussi à le mettre à jour.

macOS (app Terminal) :

```bash
curl -fsSL https://raw.githubusercontent.com/isleepverywellatnight/kino/main/packaging/install.sh | bash
```

Windows (PowerShell) :

```powershell
irm https://raw.githubusercontent.com/isleepverywellatnight/kino/main/packaging/install.ps1 | iex
```

### Si le système bloque le premier lancement

KINO n'est pas encore signé par Apple ni par Microsoft. Après un
téléchargement depuis le navigateur :

- **macOS** : si KINO refuse de s'ouvrir, allez dans Réglages Système →
  Confidentialité et sécurité, puis cliquez sur **Ouvrir quand même**.
- **Windows** : si « Windows a protégé votre ordinateur » s'affiche, cliquez
  sur **Informations complémentaires**, puis **Exécuter quand même**.

Les commandes ci-dessus évitent ces deux messages.

## Où sont les données

- `~/.kino_data.db` : bibliothèque, historique, notes et cache.
- `~/.rd_cinehub_config.json` : réglages et clés des services.
- `~/Library/Application Support/KINO` (macOS) ou `%APPDATA%\KINO` (Windows) :
  sauvegardes, journal d'erreurs. Les mises à jour en cours sont dans
  `%LOCALAPPDATA%\KINO\Updates` sur Windows.

Les clés restent sur cet ordinateur : elles ne sont ni sauvegardées ni
synchronisées. Désinstaller KINO ne supprime pas ces données.

## Organisation du dépôt

| Dossier | Contenu |
|---|---|
| `src/` | Code Python de KINO ; `desktop.py` le démarre |
| `web/` | Interface (HTML, CSS, JavaScript) |
| `addons/` | Sources supplémentaires, activables dans Paramètres |
| `assets/` | Icônes et fichiers fournis avec l'application |
| `packaging/` | Construction des applications, installateurs, scripts d'installation |
| `tools/` | Outils de construction et de publication |
| `tests/` | Tests |

Construire KINO ou publier une version : voir
[packaging/README.md](packaging/README.md).

## Licence

KINO est publié sous licence MIT (voir [LICENSE](LICENSE)). Le dossier
`web/js/vendor` contient PeerJS et QRCode.js, chacun sous sa propre licence MIT.
mpv, libmpv et FFmpeg, inclus dans les applications, sont sous licence GPL ou
LGPL.
