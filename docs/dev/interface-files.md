# Fichiers de l'interface

Le code principal de l'interface est découpé par thème en onze fichiers. Ce sont
des modules ES : chacun importe ce qu'il utilise des autres et exporte ce qu'ils
lui prennent. `web/index.html` ne charge qu'un module, `web/js/main.js`, qui les
importe dans cet ordre, avec les autres fichiers de la page, puis lance KINO.
Seul `i18n.js` reste un script classique : il écrit ses dictionnaires avec
`document.write` avant que la page ne s'affiche.

| Fichier | Contenu |
|---|---|
| `app-core.js` | État partagé, spoilers, parcours anime, configuration, appels au serveur, historique et Ma Liste, cartes « À continuer » |
| `app-home.js` | Nouveaux épisodes, rangées de l'accueil, titre du catalogue, bannière « À la une » |
| `app-lists.js` | Roulette, filtres et tri de Ma Liste et de Déjà vus, Mes séries, Calendrier |
| `app-letterboxd.js` | Import Letterboxd (profil, CSV, listes) et pages des listes importées |
| `app-tabs.js` | Discord, changement d'onglet, catalogue et cartes d'affiche |
| `app-cloud-settings.js` | Cloud par titre, notifications, enregistrement des Paramètres |
| `app-social.js` | Échappement HTML, Trakt, télécommande, Watch Party, add-ons, Explorer, marquage vu |
| `app-fiche.js` | Glisser-déposer, raccourcis, recherche, titres français, fiche d'un titre, page acteur |
| `app-sources.js` | Rangée d'épisodes, sources, débridage, lancement d'un fichier |
| `app-player.js` | Fenêtre, lecteur intégré (sous-titres, son, vitesse, image dans l'image…) |
| `app-start.js` | Touche Échap, bibliothèque hors ligne, saison hors ligne, mises à jour, clavier, sans internet, démarrage |

D'autres thèmes ont leur propre fichier, chargé à côté : `ratings.js` (noter en
un geste) et `anime-home.js` (onglet Anime : rangées « À la une », planning de la
semaine, ouverture d'une œuvre AniList sur sa fiche KINO).

Le seul code tiers est dans `web/js/vendor/` : PeerJS (Watch Party), copie exacte
du fichier publié sur npm, avec sa licence. `test_no_remote_code.js` en fige
l'empreinte, explique comment changer de version, et refuse tout script chargé
depuis internet.

## Règles

- **Imports et types :** un fichier importe chaque nom qu'il prend à un autre.
  TypeScript vérifie tous les fichiers de `web/js` (`jsconfig.json`, `checkJs`,
  bloquant en CI) : un import oublié ne se verrait sinon qu'au moment où la
  ligne s'exécute. Un élément de la page qui n'est pas un simple `HTMLElement`
  reçoit son type là où on le prend :
  `/** @type {HTMLInputElement} */ (document.getElementById('cfgToken'))`.
  Les imports ne visent que des fichiers voisins (`./app-core.js`), jamais une
  adresse (`test_no_remote_code.js`).
- **État partagé :** un module ne peut pas affecter une variable qu'il importe.
  Il appelle le setter déclaré à côté d'elle (`setCurrentMedia(media)`) ; les
  réglages du lecteur et les comptes liés sont les champs de `playerPrefs` et
  `linkedAccounts` (`app-core.js`).
- **Chargement :** au chargement, un fichier ne fait que déclarer et poser des
  écouteurs. Les modules sont évalués dans l'ordre de leurs imports, pas dans
  celui de `main.js` : rien ne doit dépendre de cet ordre.
- **Démarrage :** le code de démarrage (`checkConfig()`, `switchTab('movies')`…)
  est dans `startKino()` (`app-start.js`), que `main.js`, chargé en dernier,
  appelle une fois toutes les parties chargées.
- **Actions :** ce que le balisage déclare (`data-on-click="switchTab"`,
  `act('switchTab')`) ne lance que ce qu'un fichier a enregistré : chaque fichier
  finit par `registerActions({ … })` (`web/js/actions.js`).
  `tools/frontend_check.js` vérifie que chaque nom déclaré est enregistré.
- **Tests :** ils lisent les fichiers recollés dans l'ordre de `main.js`, sans
  leurs mots `import` et `export` (`tools/interface_source.js`) : sur une page,
  chaque nom est unique. Déplacer une fonction d'un fichier à l'autre ne les
  dérange pas tant que l'ordre du texte recollé reste le même.
- **Nouveau fichier :** pour ajouter un thème, l'importer dans `main.js` à sa
  place, puis l'ajouter à ce tableau.
