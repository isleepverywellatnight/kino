# Fichiers de l'interface

Le code principal de l'interface est découpé par thème en onze fichiers. `web/index.html` les
charge dans cet ordre, comme des scripts classiques : ils partagent les mêmes
variables et fonctions globales.

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

- **Ordre :** il compte. Une instruction exécutée au chargement (hors d'une
  fonction) ne peut appeler qu'une fonction d'un fichier déjà chargé. Les
  appels faits plus tard (clic, minuterie, réponse du serveur) peuvent viser
  n'importe quel fichier. `test_interface_split.js` le vérifie.
- **Démarrage :** le code de démarrage (`checkConfig()`, `switchTab('movies')`…)
  reste à la fin de `app-start.js`.
- **Tests :** ils lisent les fichiers recollés dans l'ordre de `index.html`
  (`tools/interface_source.js`). Déplacer une
  fonction d'un fichier à l'autre ne les dérange pas tant que l'ordre du texte
  recollé reste le même.
- **Nouveau fichier :** pour ajouter un thème, l'ajouter à `index.html` à sa
  place, puis à ce tableau.
