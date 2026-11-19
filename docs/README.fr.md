# KINO

[English](../README.md) · **Français** · [Deutsch](README.de.md) · [Español](README.es.md) · [Italiano](README.it.md) · [Português](README.pt.md) · [Русский](README.ru.md) · [中文](README.zh.md) · [日本語](README.ja.md)

KINO est une application de bureau pour macOS et Windows qui réunit au même endroit le catalogue de films, de séries et d'animes, le suivi de ce que l'on regarde et la lecture, à partir de votre propre compte chez un service de débridage (Real-Debrid, AllDebrid, TorBox, Debrid-Link, Premiumize).

L'interface existe en anglais, français, allemand, espagnol, italien, portugais, russe, chinois et japonais : KINO prend la langue de l'ordinateur, et elle se change dans Paramètres → Application → Langue de l'interface.

## Avertissement

KINO n'héberge, ne fournit et ne distribue aucun contenu. Il interroge des sources que l'utilisateur choisit et lit ce que son propre service de débridage lui donne. Chaque utilisateur est seul responsable de ce qu'il regarde et du respect du droit d'auteur dans son pays.

## Ce que fait KINO

- **Catalogue** : films, séries et animes, avec fiches, distribution, bandes-annonces, et chaque titre sous son nom original.
- **Sans service de débridage** : ajoutez des addons (plus de sources,
  et leurs catalogues dans Explorer), et lisez les torrents en P2P après avoir
  accepté ce que cela implique (votre adresse IP est visible, vous partagez
  pendant la lecture).
- **Manga** : tout le catalogue et les séries en cours de parution, chaque
  chapitre réuni depuis plusieurs sites (dans votre langue quand il existe,
  sinon en anglais), des volumes complets en HD par torrent, et vos propres
  fichiers CBZ et dossiers d’images ; lecture page par page ou en défilement,
  dans le sens japonais si vous voulez. KINO retient où vous vous êtes arrêté.
- **Lecture** : lecteur vidéo inclus, basé sur mpv (HDR, sous-titres, pistes audio, saut du générique).
- **Suivi** : Ma Liste, lectures en cours, Déjà vus, Mes séries, calendrier des sorties, notes en un geste.
- **Suggestions** : « Pour vous » et « Si vous avez aimé », calculées sur votre ordinateur à partir de ce que vous avez vu et noté.
- **Anime** : rangées tirées d'AniList, planning de la semaine, parcours d'une saga dans l'ordre de sortie.
- **Autres services** : import Letterboxd, Trakt, présence Discord.
- **À plusieurs et ailleurs** : Watch Party, lecture sur une TV du réseau local, télécommande depuis un téléphone.
- **Hors ligne** : téléchargement d'un film ou d'une saison pour le regarder sans internet.
- **Vos données** : rangées sur votre ordinateur, avec une copie de sauvegarde par jour et une synchronisation possible par Google Drive.

## Télécharger KINO

| Système | Télécharger |
|---|---|
| **macOS** — Mac Apple Silicon (M1 ou plus récent), macOS 14 ou plus récent | [**KINO-macOS.dmg**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-macOS.dmg) |
| **Windows** — Windows 10 ou 11, 64 bits | [**KINO-Windows.exe**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows.exe) |
| **Windows sur ARM** — Windows 11 sur un PC ARM (Snapdragon…) | [**KINO-Windows-arm64.exe**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows-arm64.exe) |
| **Linux** — x86_64 : Ubuntu 22.04, Debian 12, Fedora 36 ou plus récent | [**KINO-Linux-x86_64.AppImage**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Linux-x86_64.AppImage) |
| **Linux (Flatpak)** — x86_64, toute distribution avec Flatpak | [**KINO-Linux-x86_64.flatpak**](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Linux-x86_64.flatpak) |

KINO contient tout ce qu'il lui faut : lecteur vidéo, FFmpeg, interface. Il n'y a rien d'autre à installer. Il suffit ensuite de coller la clé de votre service de débridage dans Paramètres.

- **macOS** : ouvrez le fichier `.dmg` et glissez KINO dans Applications.
- **Windows** : lancez `KINO-Windows.exe`. KINO s'installe en un clic, sans droits administrateur, puis s'ouvre.
- **Linux** : rendez `KINO-Linux-x86_64.AppImage` exécutable (Propriétés → Permissions, ou `chmod +x`), puis ouvrez-le. Les vidéos se lisent dans la fenêtre de KINO, ou dans mpv s'il est installé. Les versions suivantes s'installent depuis KINO, comme sur les autres systèmes.
- **Linux (Flatpak)** : `flatpak install --user KINO-Linux-x86_64.flatpak`, puis ouvrez KINO depuis le menu. Flatpak récupère le runtime GNOME sur Flathub. Installez le nouveau fichier `.flatpak` pour mettre à jour.

Les versions suivantes s'installent depuis KINO : **Paramètres → Mises à jour**.

### Installer avec une seule commande

Cette commande télécharge la dernière version, vérifie le fichier, installe KINO et l'ouvre. Elle sert aussi à le mettre à jour.

macOS (app Terminal) :

```bash
curl -fsSL https://github.com/isleepverywellatnight/kino/releases/latest/download/install.sh | bash
```

Windows (PowerShell) :

```powershell
irm https://github.com/isleepverywellatnight/kino/releases/latest/download/install.ps1 | iex
```

### Si le système bloque le premier lancement

KINO n'est pas encore signé par Apple ni par Microsoft. Après un téléchargement depuis le navigateur :

- **macOS** : si KINO refuse de s'ouvrir, allez dans Réglages Système → Confidentialité et sécurité, puis cliquez sur **Ouvrir quand même**.
- **Windows** : si « Windows a protégé votre ordinateur » s'affiche, cliquez sur **Informations complémentaires**, puis **Exécuter quand même**.

Les commandes ci-dessus évitent ces deux messages.

## Vérifier un téléchargement

Chaque fichier d’une version est construit par GitHub Actions à partir du commit
que la version désigne. Son SHA-256 est dans `SHA256SUMS.txt` et, à partir de la
version qui suit la 1.9.0, une attestation de build indique quel workflow et
quel commit l’ont produit :

```bash
gh attestation verify KINO-macOS.dmg -R isleepverywellatnight/kino
```

## Confidentialité

KINO n’a pas de serveur à lui et ne collecte rien. Il s’adresse directement aux
services que vous connectez (votre service de débridage, Trakt, Discord, Google
Drive), aux catalogues publics qu’il lit (comme Cinemeta, AniList, MangaDex, et
TMDB avec votre clé), aux sources qu’il interroge (indexeurs, vos addons, sites
de manga), aux pairs BitTorrent quand la lecture P2P est activée, et à GitHub
pour chercher une nouvelle version au démarrage puis toutes les six heures.
Paramètres → Mises à jour désactive cette recherche.

## Où sont les données

- `~/.kino_data.db` : bibliothèque, historique, notes et cache.
- `~/.rd_cinehub_config.json` : réglages et clés des services.
- `~/Library/Application Support/KINO` (macOS) ou `%APPDATA%\KINO` (Windows) : sauvegardes, journal d'erreurs. Les mises à jour en cours sont dans `%LOCALAPPDATA%\KINO\Updates` sur Windows.

Les clés restent sur votre ordinateur : elles ne sont ni sauvegardées ni synchronisées. Désinstaller KINO ne supprime pas ces données.

## Organisation du dépôt

| Dossier | Contenu |
|---|---|
| `crates/` | Code de KINO, en Rust : `kino` (le serveur) et `kino-desktop` (l'application) |
| `web/` | Interface (HTML, CSS, JavaScript) |
| `assets/` | Icônes et fichiers fournis avec l'application |
| `packaging/` | Construction des applications, installateurs, scripts d'installation |
| `tools/` | Outils de construction et de publication |
| `tests/` | Tests |
| `docs/` | Les README dans d'autres langues |

Construire KINO ou publier une version : voir [packaging/README.md](../packaging/README.md).

## Licence

KINO est publié sous licence MIT (voir [LICENSE](../LICENSE)). Le dossier `web/js/vendor` contient PeerJS et QRCode.js, chacun sous sa propre licence MIT. mpv, libmpv et FFmpeg, inclus dans les applications, sont sous licence GPL ou LGPL.
