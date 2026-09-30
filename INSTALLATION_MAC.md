# Guide d'utilisation de KINO sur macOS (Apple Silicon M1/M2/M3/M4 & Intel)

L'application **KINO** est 100 % optimisée pour macOS avec accélération matérielle Apple WebKit (Metal), intégration Cocoa native, recherche instantanée SQLite FTS5, gestionnaire de téléchargements multithreads et lecteur vidéo complet.

---

## 💿 Méthode 1 : Image Disque `KINO.dmg` (Le standard Mac)

Le fichier `dist/KINO.dmg` est l'image disque prête à l'emploi :

1. Transférez `KINO.dmg` sur votre Mac (AirDrop, Clé USB, NAS, Google Drive...).
2. **Double-cliquez sur `KINO.dmg`** : l'image disque se monte automatiquement sur votre Mac.
3. Glissez simplement l'icône **KINO.app** dans votre dossier **Applications**.
4. Éjectez l'image disque et lancez KINO depuis votre Launchpad ou dossier Applications !

> **Note de sécurité macOS (Gatekeeper) au 1er lancement :**
> Comme KINO est un projet open source autonome sans certificat payant Apple ($99/an), macOS peut afficher :  
> *"KINO ne peut pas être ouvert car l'éveloppeur ne peut pas être vérifié"*
>
> **Pour l'autoriser en 1 seconde :**
> - **Option Graphique :** Faites un **Clic-Droit** (ou Ctrl + Clic) sur `KINO.app` > Cliquez sur **Ouvrir** > Confirmez **Ouvrir**.
> - **Option Terminal :** Ouvrez le Terminal et tapez :
>   ```bash
>   xattr -cr /Applications/KINO.app
>   ```

---

## 📦 Méthode 2 : Archive directe `KINO-macOS.zip`

Si vous préférez extraire directement l'application :
1. Décompressez `dist/KINO-macOS.zip` sur votre Mac.
2. Glissez `KINO.app` dans `/Applications`.
3. Lancez KINO ! Les permissions d'exécution POSIX (`0755`) sont déjà configurées dans l'archive.

---

## 🚀 Méthode 3 : Lanceur script `Lancer_KINO.command`

Si vous utilisez le code source complet :
1. Double-cliquez sur `Lancer_KINO.command`.
2. Le script configure automatiquement l'environnement virtuel macOS et démarre KINO Desktop.

---

## 🎬 Lecteurs multimédias pris en charge sur macOS

1. **Lecteur KINO intégré (In-App) :**
   - Fonctionne immédiatement sans aucune installation externe.
   - Moteur Apple WebKit avec accélération GPU Metal.
   - Synchronisation et personnalisation des sous-titres, changement audio dynamique, gestion des épisodes.

2. **Lecteurs externes compatibles :**
   - **IINA** (Le lecteur le plus populaire sur Mac, moteur mpv avec HDR & Touch Bar) :  
     Téléchargeable sur [iina.io](https://iina.io) ou `brew install --cask iina`.
   - **VLC pour Mac** :  
     Téléchargeable sur [videolan.org](https://www.videolan.org) ou `brew install --cask vlc`.
   - **MPV natif macOS** :  
     Installable via `brew install mpv`.

---

## 🛠️ Recompilation du DMG natif sur Mac (Optionnel)

Si vous disposez de macOS et souhaitez créer un DMG compressé UDZO via l'utilitaire système Apple `hdiutil` :
```bash
bash dist/creer_dmg_natif_mac.sh
```
