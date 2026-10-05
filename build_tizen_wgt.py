#!/usr/bin/env python3
"""
KINO Samsung Tizen Smart TV Package Builder (.wgt)
==================================================
Générateur de package autonome Tizen Web Application pour téléviseurs Samsung (2016-2026).
Crée le package KINO.wgt prêt pour le déploiement via Tizen Studio / SDB (Smart Development Bridge).
"""

import os
import sys
import shutil
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
BUILD_DIR = BASE_DIR / "build_tizen"
OUTPUT_WGT = BASE_DIR / "KINO.wgt"

TIZEN_CONFIG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<widget xmlns="http://www.w3.org/ns/widgets" 
        xmlns:tizen="http://tizen.org/pkg/tizen" 
        version="1.0.0" 
        viewmodes="maximized" 
        id="http://kino.app/widget">
    <tizen:application id="kino000001.KINO" package="kino000001" exec="index.html" screen-orientation="landscape"/>
    <tizen:app-control>
        <tizen:src name="index.html" reload="disable"/>
        <tizen:operation name="http://samsung.com/appcontrol/operation/eden_service"/>
    </tizen:app-control>
    <content src="index.html"/>
    <feature name="http://tizen.org/feature/screen.size.all"/>
    <feature name="http://tizen.org/feature/network.internet"/>
    <icon src="icon.png"/>
    <name>KINO</name>
    <tizen:profile name="tv"/>
    <tizen:setting screen-orientation="landscape" context-menu="enable" background-support="disable" encryption="disable" install-location="auto"/>
    
    <!-- Privilèges Samsung Tizen Smart TV -->
    <tizen:privilege name="http://tizen.org/privilege/internet"/>
    <tizen:privilege name="http://tizen.org/privilege/tv.audio"/>
    <tizen:privilege name="http://tizen.org/privilege/tv.display"/>
    <tizen:privilege name="http://tizen.org/privilege/tv.inputdevice"/>
    <tizen:privilege name="http://tizen.org/privilege/application.launch"/>
    <tizen:privilege name="http://developer.samsung.com/privilege/avplay"/>
    <tizen:privilege name="http://developer.samsung.com/privilege/drmplay"/>
    
    <!-- Autorisation réseau totale (Contournement total CORS sur Tizen) -->
    <access origin="*" subdomains="true"/>
    
    <tizen:metadata key="http://samsung.com/tv/metadata/use.preview" value="true"/>
    <tizen:metadata key="http://samsung.com/tv/metadata/devel.mode" value="true"/>
</widget>
"""

def build_wgt():
    print("=" * 60)
    print("  KINO - Générateur de Package Samsung Smart TV (Tizen .wgt)")
    print("=" * 60)

    # 1. Nettoyage du répertoire de build
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Copie des fichiers Web
    print("[1/4] Copie des fichiers Web de KINO...")
    for item in WEB_DIR.iterdir():
        dest = BUILD_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # Copie des scripts et dépendances additionnelles
    qrcode_file = BASE_DIR / "qrcode.min.js"
    if qrcode_file.is_file():
        shutil.copy2(qrcode_file, BUILD_DIR / "qrcode.min.js")

    # 3. Génération de config.xml
    print("[2/4] Création du manifeste Tizen TV (config.xml)...")
    config_path = BUILD_DIR / "config.xml"
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(TIZEN_CONFIG_XML.strip())

    # 4. Icône pour le Smart Hub Samsung TV
    print("[3/4] Préparation de l'icône de l'application...")
    kino_png = BASE_DIR / "kino.png"
    icon_dest = BUILD_DIR / "icon.png"
    if kino_png.is_file():
        shutil.copy2(kino_png, icon_dest)
    else:
        # Fallback icône
        with open(icon_dest, "wb") as f:
            f.write(b"")

    # 5. Création du package .wgt (archive zip standard Tizen)
    print(f"[4/4] Compression du package Tizen vers {OUTPUT_WGT.name}...")
    if OUTPUT_WGT.exists():
        OUTPUT_WGT.unlink()

    with zipfile.ZipFile(OUTPUT_WGT, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(BUILD_DIR):
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(BUILD_DIR)
                zf.write(full_path, arcname=str(rel_path).replace("\\", "/"))

    size_kb = OUTPUT_WGT.stat().st_size / 1024
    print("-" * 60)
    print(f" SUCCÈS : {OUTPUT_WGT.name} créé ({size_kb:.1f} Ko)")
    print(f" Emplacement : {OUTPUT_WGT.resolve()}")
    print("=" * 60)
    return True

if __name__ == "__main__":
    build_wgt()
