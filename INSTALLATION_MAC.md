# Guide d'utilisation de KINO sur macOS (Apple Silicon M1/M2/M3/M4 & Intel)

L'application **KINO** est désormais 100 % compatible et optimisée pour macOS !

---

## 🚀 Méthode 1 : Lancement en 1 clic (Recommandé)

1. Transférez le dossier `rd-app` sur votre Mac (via AirDrop, clé USB, Google Drive, etc.).
2. Double-cliquez sur le fichier :
   ```
   Lancer_KINO.command
   ```
3. **Au premier lancement uniquement :**
   - Le script configure automatiquement l'environnement Python pour macOS (`pywebview` + moteur natif WebKit Cocoa).
   - L'application s'ouvre ensuite dans sa fenêtre native macOS.

> **Astuce macOS au 1er clic :** Si macOS affiche *"Impossible d'ouvrir le fichier car il provient d'un développeur non identifié"* :
> - Faites un **Clic droit** sur `Lancer_KINO.command` > Cliquez sur **Ouvrir** > Confirmez **Ouvrir**.

---

## 🍏 Méthode 2 : L'application `KINO.app`

Le dossier contient également `KINO.app` :
- Vous pouvez glisser `KINO.app` dans votre dossier `/Applications` ou sur votre Bureau.
- Elle se comporte comme n'importe quelle application Mac native.

---

## 🎬 Lecteurs multimédias pris en charge sur macOS

1. **Lecteur KINO intégré (In-App) :**
   - Fonctionne directement sans rien installer.
   - Utilise le moteur matériel natif Apple WebKit (Metal / accélération GPU).
   - Contrôle du volume fluide, pistes audio, sous-titres, gestion des épisodes.

2. **Lecteurs externes recommandés pour macOS :**
   - **IINA** (Le meilleur lecteur moderne pour Mac, basé sur mpv avec support HDR Apple Silicon) :  
     Téléchargeable sur [iina.io](https://iina.io) ou via Terminal : `brew install --cask iina`
   - **VLC pour Mac** :  
     Téléchargeable sur [videolan.org](https://www.videolan.org) ou via `brew install --cask vlc`
   - **MPV natif macOS** :  
     Installable via Homebrew : `brew install mpv`

---

## 🛠️ Lancement manuel via le Terminal (Alternative)

Si vous préférez utiliser le Terminal :

```bash
cd rd-app

# 1. Installer les dépendances macOS
pip3 install -r requirements.txt

# 2. Lancer l'application
python3 desktop.py
```
