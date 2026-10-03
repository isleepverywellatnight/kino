# Workspace KINO — Streaming & Media Center (Real-Debrid & TMDB)

Bienvenue dans le workspace **KINO**.

## 📌 Présentation du Projet
**KINO** est une application multimédia autonome permettant de rechercher, streamer et télécharger des films et séries en très haute qualité via l'API **Real-Debrid** et les métadonnées **TMDB**.

L'application fonctionne :
1. En mode **Application Bureau Native** (sans bordure, fluide, Edge Chromium WebView2 sous Windows et Apple Cocoa WebKit sous macOS).
2. En mode **Web App** (serveur local sur `http://127.0.0.1:8080`).

---

## 📂 Architecture des Fichiers

| Fichier / Dossier | Rôle |
| :--- | :--- |
| **`app.py`** | Serveur HTTP backend autonome (Python standard) & interface frontend intégrée (catalogue, moteur de recherche, lecteur vidéo HTML5/HLS personnalisé, gestionnaire de téléchargements, communication avec l'API Real-Debrid & TMDB). |
| **`desktop.py`** | Fenêtre de bureau native sans bordure (`pywebview`) avec prise en charge du redimensionnement fluide sur tous les bords/coins, drag personnalisé, et raccourcis fenêtrés. Multiplateforme Windows / macOS. |
| **`cli.py`** | Outil en ligne de commande pour interagir avec le service. |
| **`Lancer_KINO.bat`** | Lanceur Windows en 1 clic. |
| **`Lancer_KINO_Silencieux.vbs`** | Lanceur Windows discret sans invite de commande. |
| **`Lancer_KINO.command`** | Lanceur macOS double-cliquable dans le Finder. |
| **`KINO.app`** | Bundle d'application macOS natif. |
| **`requirements.txt`** | Dépendances Python minimales (`pywebview`). |
| **`.agents/skills/`** | Compétences d'ingénierie et design actives (`frontend-design`, `canvas-design`, etc.). |

---

## 🎬 Lecteurs Pris en Charge
- **KINO In-App Player :** Lecteur intégré dans l'interface, contrôle fluide du volume, sélection des pistes audio et sous-titres, enchaînement automatique des épisodes.
- **Lecteurs externes compatibles :** MPV (avec IPC socket), IINA (macOS), VLC.

---

## 💡 Règles & Directives de Développement
- **Dépendances minimales :** Privilégier la bibliothèque standard Python et `pywebview`.
- **Compatibilité multiplateforme :** Toujours garantir le bon fonctionnement sous Windows et macOS.
- **Ergonomie UI :** Interface sombre (Dark Theme), moderne, sans éléments superflus, typographie travaillée et animations fluides.
- **Synchronisation Google Drive automatique :** À chaque modification de code ou commit dans le projet, toujours synchroniser automatiquement les changements vers Google Drive (`python sync_drive.py`) sans attendre que l'utilisateur le demande. Ne jamais exécuter cette synchronisation de code au lancement de l'application.
