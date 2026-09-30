#!/usr/bin/env python3
"""
Script de compilation autonome KINO pour Windows (PyInstaller)
Produit le dossier distribuable dist/KINO/ avec KINO.exe
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def build():
    print("=" * 60)
    print("       COMPILATION DE KINO DESKTOP (Windows .exe)")
    print("=" * 60)
    print()

    # Nettoyage des dossiers de build précédents
    build_dir = ROOT / "build"
    kino_dist = ROOT / "dist" / "KINO"
    
    if build_dir.exists():
        print("[1/4] Nettoyage du dossier build/...")
        shutil.rmtree(build_dir, ignore_errors=True)
    if kino_dist.exists():
        print("[2/4] Nettoyage du dossier dist/KINO/...")
        shutil.rmtree(kino_dist, ignore_errors=True)

    print("[3/4] Lancement de PyInstaller avec kino.spec...")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        str(ROOT / "kino.spec"),
    ]
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode != 0:
        print()
        print("[ERREUR] La compilation PyInstaller a échoué.")
        sys.exit(res.returncode)

    exe_path = ROOT / "dist" / "KINO" / "KINO.exe"
    if exe_path.exists():
        print()
        print("=" * 60)
        print(" [SUCCÈS] KINO.exe a été compilé avec succès !")
        print(f" Emplacement : {exe_path}")
        print("=" * 60)
    else:
        print("[ATTENTION] Le fichier KINO.exe n'a pas été trouvé dans dist/KINO.")

if __name__ == "__main__":
    build()
