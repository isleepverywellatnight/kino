#!/usr/bin/env python3
"""
KINO Samsung Smart TV Deployer & Assistant
==========================================
Script interactif pour déployer KINO.wgt directement sur une Samsung Smart TV en Wi-Fi.
Gère la découverte SDB, la connexion au port 26101 de la TV et l'installation du package.
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WGT_FILE = BASE_DIR / "KINO.wgt"

def find_sdb_tool():
    """Recherche l'exécutable sdb (Smart Development Bridge) de Samsung."""
    cmd = shutil.which("sdb")
    if cmd:
        return cmd
    common_paths = [
        Path.home() / "tizen-studio" / "tools" / "sdb.exe",
        Path("C:/tizen-studio/tools/sdb.exe"),
        Path("C:/Program Files/tizen-studio/tools/sdb.exe"),
        Path.home() / "AppData" / "Local" / "tizen-studio" / "tools" / "sdb.exe",
    ]
    for p in common_paths:
        if p.is_file():
            return str(p)
    return None

def find_tizen_tool():
    """Recherche l'exécutable tizen CLI."""
    cmd = shutil.which("tizen")
    if cmd:
        return cmd
    common_paths = [
        Path.home() / "tizen-studio" / "tools" / "ide" / "bin" / "tizen.bat",
        Path("C:/tizen-studio/tools/ide/bin/tizen.bat"),
        Path("C:/Program Files/tizen-studio/tools/ide/bin/tizen.bat"),
    ]
    for p in common_paths:
        if p.is_file():
            return str(p)
    return None

def main():
    print("=" * 64)
    print("  KINO - Assistant Déploiement Samsung Smart TV (Tizen OS)")
    print("=" * 64)

    # 1. Vérifier la présence du package .wgt
    if not WGT_FILE.is_file():
        print("[!] Le fichier KINO.wgt n'a pas été trouvé. Compilation en cours...")
        import build_tizen_wgt
        build_tizen_wgt.build_wgt()

    print(f"\n[+] Package prêt : {WGT_FILE.name} ({WGT_FILE.stat().st_size / 1024:.1f} Ko)")

    # 2. Vérification des outils Samsung Tizen
    sdb_path = find_sdb_tool()
    tizen_path = find_tizen_tool()

    if not sdb_path:
        print("\n" + "-" * 64)
        print("  COMMENT ACTIVER ET INSTALLER KINO SUR VOTRE TV SAMSUNG")
        print("-" * 64)
        print("""
1. Activer le Mode Développeur sur la TV Samsung :
   - Allumez votre TV Samsung et ouvrez le menu 'Apps'.
   - Sur votre télécommande, tapez la suite de chiffres : 1 2 3 4 5
   - Un pop-up bleu 'Developer Mode' apparaît !
   - Basculez le commutateur sur 'ON'.
   - Dans le champ 'Host PC IP', entrez l'IP de votre PC (ex: 192.168.1.89).
   - Redémarrez la TV (maintenez le bouton Power de la télécommande 3 secondes).

2. Deux méthodes pour installer le package KINO.wgt sur votre TV :
   A. Via Tizen Studio / CLI officiel (Samsung) :
      - Téléchargez Tizen Studio (gratuit) : https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk.html
      - Ouvrez Device Manager, ajoutez l'IP de votre TV, puis installez KINO.wgt.

   B. MÉTHODE SANS RIEN INSTALLER (Recommandée & Immédiate) :
      - Sur votre TV Samsung, ouvrez l'application 'Internet' (navigateur web).
      - Entrez l'adresse de votre PC : http://192.168.1.89:8080
      - Tout fonctionne déjà avec la télécommande Samsung grâce au pont Tizen !
        """)
        print("-" * 64)
        input("Appuyez sur Entrée pour quitter...")
        return

    # Si SDB est installé :
    tv_ip = input("\nEntrez l'adresse IP de votre TV Samsung (ex: 192.168.1.50) : ").strip()
    if not tv_ip:
        print("[-] Aucune IP renseignée. Abandon.")
        return

    print(f"\n[1/3] Connexion à la TV Samsung sur {tv_ip}:26101...")
    try:
        res = subprocess.run([sdb_path, "connect", f"{tv_ip}:26101"], capture_output=True, text=True, check=False)
        print(res.stdout)
    except Exception as e:
        print(f"Erreur de connexion SDB : {e}")

    print("\n[2/3] Vérification des périphériques connectés...")
    try:
        subprocess.run([sdb_path, "devices"], check=False)
    except Exception:
        pass

    if tizen_path:
        print(f"\n[3/3] Installation de {WGT_FILE.name} sur la TV...")
        try:
            res_install = subprocess.run([tizen_path, "install", "-n", str(WGT_FILE), "-t", tv_ip], capture_output=True, text=True, check=False)
            print(res_install.stdout)
            print(res_install.stderr)
            print("\n[✓] Déploiement terminé ! Vérifiez l'écran d'accueil de votre TV Samsung.")
        except Exception as e:
            print(f"Erreur lors de l'installation : {e}")
    else:
        print(f"\n[3/3] Envoi du package via SDB install...")
        try:
            subprocess.run([sdb_path, "-s", tv_ip, "install", str(WGT_FILE)], check=False)
            print("\n[✓] Déploiement terminé ! Vérifiez l'écran d'accueil de votre TV Samsung.")
        except Exception as e:
            print(f"Erreur lors du déploiement : {e}")

if __name__ == "__main__":
    main()
