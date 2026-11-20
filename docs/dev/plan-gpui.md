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

## État de G0

- **Fenêtre et grille** (fait) : fenêtre sans cadre avec la barre KINO, grille
  virtualisée de 300 affiches décodées hors du fil principal. Défilement
  automatique à 4 000 px/s mesuré en CI :

  | Runner | Images | Médiane | p95 | > 33 ms |
  |---|---|---|---|---|
  | macOS 15 (vraie puce graphique) | 462 | 16,7 ms (60 i/s) | 19,5 ms | 4 |
  | Windows (rendu logiciel WARP, sans GPU) | 358 | 21,5 ms | 40,8 ms | 29 |
  | Linux (Vulkan logiciel lavapipe) | 229 | 32 ms | 32 ms | 2 |

  Seul le Mac a une vraie puce graphique : il tient 60 images/s. Les deux autres
  mesurent un GPU émulé par le processeur, pas gpui.

  **Sur le PC Windows de l'utilisateur** (vraie carte graphique, écran à haute
  fréquence) : 1 594 images, médiane 5,0 ms, p95 5,6 ms, pire image 28,9 ms,
  aucune au-dessus de 33 ms. Feu vert pour la fluidité de la grille.
- **Vidéo sous les contrôles gpui** (fait, voie universelle) : le rendu
  logiciel de libmpv dessine chaque image en mémoire, gpui l'affiche sous ses
  contrôles (`crates/kino-ui/src/video.rs`, même code sur les 3 systèmes). La CI
  lit une mire 1080p avec les libmpv publiées par KINO et garde une capture.
- **Voie native Windows** (`--video --native`, `native.rs`) : mpv dessine au
  GPU dans une fenêtre enfant désactivée, sous la surface gpui transparente ;
  un vrai clic sur le bouton lecture au-dessus de la vidéo arrive à gpui
  (vérifié en CI). Chaque mesure donne aussi `cpu_pct`.
- **Voie native macOS** (`mac_native.rs`) : la vue OpenGL de mpv sous la vue
  de gpui, comme le lecteur de la version web. Elle fonctionne en CI, mais les
  machines virtuelles macOS de GitHub n'accélèrent pas OpenGL (Metal oui) :
  ~600 ms par image en rendu logiciel, la vitesse se mesure sur un vrai Mac.
  À faire : dessiner hors du fil principal (contexte OpenGL partagé, comme
  IINA), pour qu'un dessin lent ne fige jamais l'interface.
- **Linux** : la voie universelle (`video.rs`) suffit pour G0. Une voie native
  demande un compositeur (X11) ou une sous-surface Wayland ; elle viendra en G3
  si la mesure sur une vraie machine le justifie.
- **CPU au repos** (`--idle`) : la grille affichée, rien ne bouge, mesuré 10 s.
  En CI : Windows 0,2–0,5 %, Linux 0,2 %, macOS 0,7–1,3 %. Sur macOS, gpui
  laisse tourner son `CVDisplayLink` tant que la fenêtre est visible (60 réveils
  par seconde sans rien dessiner) : c'est ce plancher. La CI vérifie < 1 % sur
  Windows et Linux, < 2 % sur macOS. **À faire** : mettre gpui en pause quand
  rien n'est à redessiner (version plus récente de gpui, ou correctif local).
