# 🎬 KINO Desktop — Guide d'installation & Utilisation (Windows)

Bienvenue sur **KINO Desktop** pour Windows !
Cette archive contient l'application complète, autonome et prête à l'emploi.

---

## ⚡ Lancement Rapide (1-Clic)

1. **Prérequis** :
   - Assurez-vous d'avoir **Python 3.10, 3.11 ou 3.12+** installé sur votre PC Windows.
   - Si vous ne l'avez pas encore, téléchargez-le sur [python.org/downloads](https://www.python.org/downloads/).
   - ⚠️ **Très important lors de l'installation de Python** : cochez impérativement la case **« Add Python to PATH »** (ou « Ajouter Python aux variables d'environnement ») sur la première page de l'installateur.

2. **Démarrage en 1 clic** :
   - Double-cliquez sur `Lancer_KINO.bat`.
   - Au tout premier démarrage, le script configure automatiquement un environnement virtuel local (`.venv_win`) et installe les bibliothèques requises (`pywebview`) en quelques secondes.
   - L'interface native KINO s'ouvre automatiquement dans sa fenêtre fluide avec accélération matérielle Edge Chromium (WebView2).
   - *(Optionnel)* Pour lancer l'application directement sans fenêtre d'invite de commande noire en arrière-plan, vous pouvez double-cliquer sur `Lancer_KINO_Silencieux.vbs`.

---

## 🎥 Choix du Lecteur Vidéo (Windows)

Dans les **Paramètres (⚙️)** de KINO :

1. **Lecteur KINO / mpv (Recommandé pour 4K HDR & Dolby Vision)** :
   - KINO détecte automatiquement `mpv.exe` ou `vlc.exe` s'il est présent sur votre ordinateur.
   - Pour installer **mpv** facilement sur Windows :
     - Soit via WinGet dans un terminal : `winget install mpv.net` ou `winget install shinchiro.mpv`
     - Soit en téléchargeant l'archive mpv depuis [mpv.io/installation/](https://mpv.io/installation/) et en extrayant le dossier dans vos Téléchargements ou dans `C:\Program Files\mpv\`.
   - Pour installer **VLC** : `winget install VideoLAN.VLC` ou depuis le site officiel [videolan.org](https://www.videolan.org/).

2. **Lecteur intégré** :
   - Si vous ne souhaitez installer aucun logiciel externe, choisissez **« Lecteur intégré »** dans les paramètres : la vidéo sera lue directement à l'intérieur de la fenêtre de KINO.

---

## 🚀 Fonctionnalités Clés Incluses

- **Multi-Débrideurs supportés** : Real-Debrid, AllDebrid, TorBox, Debrid-Link, Premiumize, Mega-Debrid avec détection du cache instantané (RD+, AD+, TB+, etc.).
- **Synchronisation Letterboxd en 1 clic** : Importez automatiquement votre Watchlist (dans *Ma Liste*) et vos films visionnés (dans *Déjà vus*) par pseudo ou fichier `.csv`.
- **Tirage au sort KINO (Roulette)** : Animation fluide inspirée de CS:GO avec bruitages mécaniques Web Audio (bruit de roulement + jingle de victoire).
- **Catégorie « Classiques »** : Sélection des chefs-d'œuvre incontournables du 7ème art avec badge de progression et exclusion des films déjà vus.
- **Tri intelligent des sources** : Classement par Seeders (Max), Taille (Plus grand / Plus léger) ou Recommandé (score de cache, langue et débit).
- **Filtre Anti-CAM & Débits frauduleux** : Élimination automatique des faux torrents 4K/1080p enregistrés au téléphone en salle de cinéma ou trop légers pour leur durée.
- **Étalonnage Image & Audio** : Choix simplifié entre SDR et HDR / Dolby Atmos, avec boost des voix et normalisation nocturne.

---

## 📂 Contenu de ce dossier

- `app.py` : Serveur applicatif KINO, APIs Stremio/Cinemeta, streaming débridé & scraper.
- `desktop.py` : Client de bureau natif (pywebview + Edge Chromium WebView2).
- `cli.py` : Interface en ligne de commande optionnelle.
- `requirements.txt` : Dépendances Python pour Windows.
- `Lancer_KINO.bat` : Lanceur automatique pour Windows.
- `Lancer_KINO_Silencieux.vbs` : Raccourci de lancement sans console.
- `HISTORIQUE_CONVERSATION.md` : Retranscription détaillée de l'ensemble de notre échange et de la conception.
- `conversation_transcript.jsonl` : Données brutes du transcript.
