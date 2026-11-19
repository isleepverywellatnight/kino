# KINO en natif Rust avec gpui

Décision du 19/11/2026 : l'interface quitte le moteur web (WebView2, WKWebView,
WebKitGTK) pour une interface native en Rust, avec **gpui** (le cadre de Zed :
rendu GPU Metal / DirectX / Vulkan, mise en page flexbox par Taffy) et
**gpui-component** (champs, listes virtualisées, menus, défilement).

Pourquoi : chaque pont typé à la compilation (l'interface appelle les fonctions
du crate `kino` au lieu de JSON par HTTP), un rendu GPU sans coût CSS caché, et
plus de HTML assemblé à la main.

Ce qui reste web : la télécommande du téléphone (`remote.html`), servie par le
serveur Rust ; le navigateur du téléphone n'a pas d'autre choix.

## Principe de migration

- Nouveau crate `crates/kino-ui` (gpui). L'app web actuelle continue d'être
  publiée tant que le natif n'a pas la parité : pas de version sans interface.
- L'interface appelle `kino::*` directement (bibliothèque, méta, sources,
  lecteur) sur le runtime tokio déjà présent ; le serveur HTTP reste pour la
  télécommande, la TV et Trakt.
- Traductions : les 9 catalogues actuels (`web/js/i18n/*.js`, indexés par clé)
  convertis en données Rust au build ; une clé inconnue est une erreur de
  compilation (macro `t!`).
- Chaque écran a ses tests (état → rendu, avec gpui `TestAppContext`) et un
  budget de fluidité mesuré en CI (temps d'image, CPU au repos).

## Phases

| Phase | Contenu | Sortie |
|---|---|---|
| **G0** Essai décisif | `kino-ui` compile et s'ouvre sur macOS, Windows (x64, ARM64), Linux en CI ; fenêtre sans cadre avec barre KINO (déplacer, agrandir, fermer) ; grille virtualisée de 300 affiches chargées du réseau, défilement rapide mesuré ; **vidéo mpv sous des contrôles gpui** sur les 3 systèmes | Feu vert si : défilement sans image > 16 ms sur le runner, CPU au repos < 1 %, vidéo visible sous les contrôles partout. Sinon, rapport et alternative avant d'aller plus loin |
| **G1** Socle | Coquille (barre latérale, en-tête, recherche, navigation, retour), thème (couleurs et tailles de `design.css`), `t!` et les 9 langues, cache d'images, état partagé typé, raccourcis clavier | Navigation complète entre écrans vides |
| **G2** Découvrir | Films, Séries, Anime (accueil, rangées, catalogue, genres, tri), Ce soir, Explorer, recherche | Parité visuelle et fonctionnelle avec la version web |
| **G3** Fiche et lecture | Fiche (saisons, épisodes, sources, notes), lancement en un clic, lecteur intégré mpv avec ses contrôles (pistes, sous-titres, vitesse, intro, épisode suivant), lecteur externe, TV | Lecture native identique sur les 3 systèmes |
| **G4** Bibliothèque | Ma Liste, En cours, Mes séries, Calendrier, Vu, Hors ligne, Listes, Cloud RD, Statistiques | Parité |
| **G5** Manga et le reste | Manga (catalogue, lecteur), parcours anime, Watch Party, réglages complets, mises à jour, consentements | Parité |
| **G6** Bascule | Version qui ouvre l'interface native par défaut (l'ancienne en secours une version), puis retrait de `web/` sauf la télécommande | Une version publiée, retours utilisateur |

## Risques connus

- **Vidéo** : gpui dessine tout sur le GPU. Pistes : mpv dans une fenêtre
  enfant sous la surface gpui transparente (Windows `wid`, comme aujourd'hui) ;
  macOS : vue OpenGL de mpv sous la vue Metal de gpui ; Linux : X11 `wid` /
  Wayland sous-surface. G0 tranche.
- **gpui est jeune** (0.2) : API qui bouge. Version épinglée, mise à jour
  volontaire.
- **Accessibilité** : gpui n'expose pas encore tout aux lecteurs d'écran.
- **Durée** : plusieurs mois pour G1–G5. Les correctifs urgents continuent sur
  la version web jusqu'à G6.
