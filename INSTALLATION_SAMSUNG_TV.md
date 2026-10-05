# 📺 KINO pour Samsung Smart TV (Tizen OS)

Ce guide détaille comment installer et utiliser **KINO** directement sur votre téléviseur connecté **Samsung Smart TV** (modèles 2016 à 2026 sous Tizen OS).

---

## 🌟 Deux Façons d'en profiter sur votre Samsung TV

| Méthode | Installation sur la TV | Autonomie | Télécommande Samsung |
| :--- | :--- | :--- | :--- |
| **1. Application Native (`.wgt`)** | Oui (Tuile sur la Smart Hub) | Se connecte en Wi-Fi à votre PC | 100% Native (Play, Pause, Retour, Couleurs) |
| **2. Navigateur TV Internet** | Aucune (Immédiate) | Se connecte en Wi-Fi à votre PC | 100% Fonctionnelle via `SpatialNav` |

---

## 🚀 Méthode 1 : Installer l'application native KINO (`.wgt`)

Toutes les Smart TV Samsung permettent d'installer des applications personnalisées grâce au **Mode Développeur Tizen** officiel.

### Étape 1 : Activer le Mode Développeur sur la TV Samsung
1. Allumez votre TV Samsung et appuyez sur la touche **Home** (Maison) de la télécommande.
2. Allez sur **Apps**.
3. Une fois dans le magasin d'applications, tapez simplement la suite de touches :
   ```text
   1  2  3  4  5
   ```
4. Une fenêtre bleue **Developer Mode** s'affiche !
5. Activez l'option en passant le curseur sur **ON**.
6. Dans le champ **Host PC IP**, saisissez l'adresse IP de votre PC :
   ```text
   192.168.1.89
   ```
7. Appuyez sur **OK** puis redémarrez votre TV (maintenez le bouton **Power** de la télécommande pendant 3 à 4 secondes jusqu'au redémarrage complet de l'écran).

---

### Étape 2 : Générer le package KINO
Sur votre ordinateur, ouvrez un terminal dans le dossier KINO et lancez :
```bash
python build_tizen_wgt.py
```
Le fichier **`KINO.wgt`** est généré automatiquement à la racine du projet.

---

### Étape 3 : Envoyer l'application sur la TV
Lancez l'assistant de déploiement automatique :
```bash
python deploy_samsung_tv.py
```
L'assistant vous demandera simplement l'IP de votre TV (visible dans les paramètres réseau de la TV) et installera le package sans fil via le protocole Samsung SDB.

> **Note :** Si vous préférez une interface graphique officielle Samsung, vous pouvez également utiliser **Tizen Studio Device Manager** :
> 1. Ouvrez Device Manager dans Tizen Studio.
> 2. Cliquez sur l'icône de scan réseau pour détecter votre TV.
> 3. Faites un clic droit sur la TV ➔ **Install Package** ➔ Sélectionnez `KINO.wgt`.
> L'icône **KINO** apparaît immédiatement sur la barre d'accueil Smart Hub de votre télé !

---

## ⚡ Méthode 2 : Utilisation Immédiate (Sans rien installer)

Si vous ne souhaitez pas activer le mode développeur de la TV :

1. Laissez KINO ouvert sur votre ordinateur (`Lancer_KINO.bat` ou `python app.py`).
2. Sur votre téléviseur Samsung, ouvrez l'application **Internet** (le navigateur web Samsung préinstallé).
3. Dans la barre d'URL, tapez :
   ```text
   http://192.168.1.89:8080
   ```
4. Ajoutez la page dans les **Favoris** de la TV pour un accès en 1 clic.

---

## 🎮 Commandes de la Télécommande Samsung

KINO intègre le pont [`tizen_bridge.js`](file:///c:/Users/seidk/Documents/KINO/web/js/tizen_bridge.js) qui reconnaît nativement les boutons de la télécommande Samsung :

| Bouton Télécommande Samsung | Action dans KINO |
| :--- | :--- |
| **Flèches Directionnelles (▲ ▼ ◄ ►)** | Déplacement spatial fluide dans le catalogue |
| **Bouton Central (OK / Sélection)** | Ouvrir la fiche / Lancer la lecture / Valider |
| **Touche Retour (Return / Back)** | Fermer le lecteur / Revenir en arrière |
| **Touche Play / Pause** | Mettre en pause ou reprendre la vidéo |
| **Avance Rapide (>>)** | Sauter de 30 secondes en avant |
| **Retour Rapide (<<)** | Reculer de 30 secondes |
| **Bouton Rouge (A)** | Ajouter ou Retirer le média de **Ma Liste** |
| **Bouton Vert (B)** | Ouvrir instantanément la barre de recherche |
| **Bouton Jaune (C)** | Ouvrir les Réglages KINO |
| **Bouton Bleu (D)** | Ouvrir l'Aide et la liste des commandes |

---

## 🛡️ Anti-Mise en Veille Automatique
Pendant la lecture vidéo dans KINO, le pont Tizen désactive automatiquement l'écran de veille de la TV Samsung via la fonction native `webapis.appcommon.setScreenSaver`. Votre écran ne s'éteindra jamais pendant un film.
