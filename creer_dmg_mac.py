#!/usr/bin/env python3
"""
Générateur de distribution macOS pour KINO :
1. Crée le bundle d'application natif complet KINO.app (autonome avec toutes les ressources).
2. Génère l'image disque macOS KINO.dmg (avec volume 'KINO', Rock Ridge, symlink vers /Applications).
3. Génère l'archive KINO-macOS.zip (avec attributs POSIX exécutables 0755 préservés).
4. Fournit le script creer_dmg_natif_mac.sh pour recompilation hdiutil native sur Mac si besoin.
"""

import io
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path
from PIL import Image
import pycdlib

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist"
APP_NAME = "KINO.app"
DIST_APP_DIR = DIST_DIR / APP_NAME
ROOT_APP_DIR = BASE_DIR / APP_NAME


def ensure_icns():
    """Génère AppIcon.icns à partir de kino.png si nécessaire."""
    png_path = BASE_DIR / "kino.png"
    icns_path = BASE_DIR / "AppIcon.icns"
    if png_path.exists():
        try:
            im = Image.open(png_path)
            im.save(icns_path, format="ICNS")
            print(f" [OK] Icône Apple générée : {icns_path}")
        except Exception as e:
            print(f" [!] Erreur lors de la génération ICNS : {e}")
    return icns_path if icns_path.exists() else None


def build_app_bundle(target_dir: Path):
    """Construit le bundle KINO.app autonome dans target_dir."""
    print(f"--> Construction du bundle {APP_NAME} dans {target_dir}...")
    if target_dir.exists():
        shutil.rmtree(target_dir)

    contents = target_dir / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"

    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    # 1. Info.plist
    plist_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>KINO</string>
    <key>CFBundleDisplayName</key>
    <string>KINO</string>
    <key>CFBundleIdentifier</key>
    <string>com.kino.desktop</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleSignature</key>
    <string>????</string>
    <key>CFBundleExecutable</key>
    <string>KINO</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
        <key>NSAllowsLocalNetworking</key>
        <true/>
    </dict>
</dict>
</plist>
"""
    (contents / "Info.plist").write_text(plist_content, encoding="utf-8")
    (contents / "PkgInfo").write_text("APPL????", encoding="utf-8")

    # 2. Script de lancement exécutable Contents/MacOS/KINO
    launcher_sh = """#!/bin/bash
# ==============================================================================
# KINO macOS Launcher
# ==============================================================================

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Répertoire des ressources de l'application
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../Resources" && pwd)"
cd "$DIR"

# Recherche d'un interpréteur Python 3 valide sur le Mac
PYTHON_BIN=""
for candidate in \
    "/opt/homebrew/bin/python3" \
    "/usr/local/bin/python3" \
    "/usr/bin/python3" \
    "$(which python3 2>/dev/null)"; do
    if [ -x "$candidate" ]; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    osascript -e 'display alert "Python 3 requis pour KINO" message "Veuillez installer Python 3 sur votre Mac pour utiliser KINO.\\n\\nTéléchargement gratuit : https://www.python.org/downloads/macos/\\nOu avec Homebrew dans le Terminal : brew install python" as critical'
    open "https://www.python.org/downloads/macos/"
    exit 1
fi

# Vérification et initialisation de l'environnement pywebview si nécessaire
if ! "$PYTHON_BIN" -c "import webview" 2>/dev/null; then
    USER_VENV="$HOME/.kino/venv_mac"
    mkdir -p "$HOME/.kino"
    
    if [ ! -d "$USER_VENV" ] || [ ! -f "$USER_VENV/bin/python3" ]; then
        osascript -e 'display notification "Initialisation de KINO en cours (installation automatique de pywebview)..." with title "KINO Desktop"' 2>/dev/null || true
        "$PYTHON_BIN" -m venv "$USER_VENV"
        "$USER_VENV/bin/pip" install --upgrade pip --quiet
        "$USER_VENV/bin/pip" install -r requirements.txt --quiet
    fi
    PYTHON_BIN="$USER_VENV/bin/python3"
fi

# Lancement de KINO Desktop
exec "$PYTHON_BIN" "$DIR/desktop.py"
"""
    launcher_file = macos / "KINO"
    launcher_file.write_text(launcher_sh, encoding="utf-8")
    try:
        launcher_file.chmod(launcher_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass

    # 3. Copie des fichiers et dépendances dans Resources
    files_to_copy = [
        "app.py",
        "desktop.py",
        "kino_db.py",
        "torrent_engine.py",
        "remote_controller.py",
        "trakt_engine.py",
        "addon_manager.py",
        "anime_engine.py",
        "community_lists.py",
        "requirements.txt",
        "kino.png",
        "kino.ico",
        "Lancer_KINO.command",
    ]
    for fname in files_to_copy:
        src = BASE_DIR / fname
        if src.exists():
            shutil.copy2(src, resources / fname)

    # Copie du dossier addons
    src_addons = BASE_DIR / "addons"
    if src_addons.exists():
        dst_addons = resources / "addons"
        if dst_addons.exists():
            shutil.rmtree(dst_addons)
        shutil.copytree(src_addons, dst_addons)

    icns_file = ensure_icns()
    if icns_file and icns_file.exists():
        shutil.copy2(icns_file, resources / "AppIcon.icns")
        shutil.copy2(icns_file, resources / "kino.icns")

    icon_png = BASE_DIR / "kino.png"
    if icon_png.exists():
        shutil.copy2(icon_png, resources / "AppIcon.png")

    print(f" [OK] {APP_NAME} généré avec succès.")


def generate_instructions():
    """Génère le fichier LISEZMOI_INSTALLATION.txt."""
    content = """================================================================================
                    KINO Desktop pour macOS (Version 1.0.0)
================================================================================

BIENVENUE DANS KINO !
KINO est votre media center autonome optimisé pour macOS (Apple Silicon M1/M2/M3/M4 & Intel).

--------------------------------------------------------------------------------
1. INSTALLATION
--------------------------------------------------------------------------------
- Faites simplement glisser l'icône 'KINO.app' dans votre dossier 'Applications'
  (ou lancez-la directement depuis cette image disque).

--------------------------------------------------------------------------------
2. PREMIER LANCEMENT & SÉCURITÉ MACOS (GATEKEEPER)
--------------------------------------------------------------------------------
Comme l'application n'a pas été signée avec un certificat Apple payant ($99/an),
macOS peut afficher l'avertissement classique :
"KINO ne peut pas être ouvert car l'éveloppeur ne peut pas être vérifié"

Pour l'autoriser en 2 secondes :
Option A (Graphique) :
  1. Faites un CLIC-DROIT (ou Contrôle + Clic) sur 'KINO.app'.
  2. Cliquez sur 'Ouvrir' dans le menu contextuel.
  3. Cliquez sur 'Ouvrir' dans la fenêtre de confirmation.
  (Cette manipulation n'est nécessaire qu'une seule fois !)

Option B (Terminal) :
  Ouvrez le Terminal et collez la commande suivante :
  xattr -cr /Applications/KINO.app

--------------------------------------------------------------------------------
3. PRÉREQUIS SYSTÈME
--------------------------------------------------------------------------------
- macOS 10.15 (Catalina) ou version plus récente (Big Sur, Monterey, Ventura, Sonoma, Sequoia).
- Python 3 installé (déjà présent sur la majorité des Mac, ou installable en 1 clic via
  https://www.python.org/downloads/macos/ ou 'brew install python').
- L'application installe automatiquement pywebview dans votre dossier personnel
  (~/.kino/venv_mac) au premier lancement sans toucher à votre système.

--------------------------------------------------------------------------------
4. FONCTIONNALITÉS INCLUSES DANS CETTE VERSION
--------------------------------------------------------------------------------
- Recherche instantanée TMDB + cinemeta avec autocomplétion SQLite FTS5 ultra-rapide.
- Bouton d'action 'Play' épuré et moderne sur toutes les fiches.
- Lecteur vidéo intégré avec synchronisation audio/sous-titres et redimensionnement.
- Handoff vers lecteur externe (MPV / IINA / VLC) en 1 clic avec contrôle socket IPC.
- Téléchargements directs multithreads haute vitesse via Real-Debrid.
- Mode sombre complet, bordures arrondies et intégration native Apple Cocoa WebKit.

Bon visionnage avec KINO !
"""
    readme_path = DIST_DIR / "LISEZMOI_INSTALLATION.txt"
    readme_path.write_text(content, encoding="utf-8")
    return readme_path


def build_dmg(app_path: Path, readme_path: Path):
    """Génère KINO.dmg avec pycdlib (Rock Ridge + Joliet + Symlink vers /Applications)."""
    dmg_path = DIST_DIR / "KINO.dmg"
    print(f"--> Génération de {dmg_path.name}...")
    if dmg_path.exists():
        dmg_path.unlink()

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, rock_ridge="1.09", joliet=3, vol_ident="KINO")

    # 1. Ajout du lien symbolique vers /Applications (expérience classique Mac)
    try:
        iso.add_symlink(
            symlink_path="/APPLICAT;1",
            rr_symlink_name="Applications",
            rr_path="/Applications",
        )
    except Exception as e:
        print(f" [!] Avertissement symlink Applications : {e}")

    # 2. Ajout du fichier LISEZMOI
    if readme_path.exists():
        iso.add_file(
            str(readme_path),
            "/LISEZMOI_TXT;1",
            rr_name="LISEZMOI_INSTALLATION.txt",
            joliet_path="/LISEZMOI_INSTALLATION.txt",
            file_mode=0o644,
        )

    # 3. Parcours récursif de KINO.app
    dir_counter = 0
    file_counter = 0

    def add_tree(local_dir: Path, iso_parent: str, joliet_parent: str):
        nonlocal dir_counter, file_counter
        for item in sorted(local_dir.iterdir()):
            if item.is_dir():
                dir_counter += 1
                iso_dname = f"D_{dir_counter:04d}"
                iso_dir_path = f"{iso_parent}/{iso_dname}"
                joliet_dir_path = f"{joliet_parent}/{item.name}"
                iso.add_directory(
                    iso_dir_path,
                    rr_name=item.name,
                    joliet_path=joliet_dir_path,
                    file_mode=0o755,
                )
                add_tree(item, iso_dir_path, joliet_dir_path)
            else:
                file_counter += 1
                iso_fname = f"F_{file_counter:04d};1"
                iso_file_path = f"{iso_parent}/{iso_fname}"
                joliet_file_path = f"{joliet_parent}/{item.name}"
                # Permettre l'exécution du lanceur binaire/bash
                is_exec = item.name == "KINO" or item.suffix in (".sh", ".command")
                file_mode = 0o755 if is_exec else 0o644
                iso.add_file(
                    str(item),
                    iso_file_path,
                    rr_name=item.name,
                    joliet_path=joliet_file_path,
                    file_mode=file_mode,
                )

    # Racine du bundle dans l'image
    iso.add_directory("/KINO_APP", rr_name="KINO.app", joliet_path="/KINO.app", file_mode=0o755)
    add_tree(app_path, "/KINO_APP", "/KINO.app")

    iso.write(str(dmg_path))
    iso.close()
    dmg_size_mb = dmg_path.stat().st_size / (1024 * 1024)
    print(f" [OK] {dmg_path.name} créé ({dmg_size_mb:.2f} Mo) avec {file_counter} fichiers et {dir_counter} dossiers.")


def build_zip(app_path: Path, readme_path: Path):
    """Génère KINO-macOS.zip avec permissions POSIX 0755."""
    zip_path = DIST_DIR / "KINO-macOS.zip"
    print(f"--> Génération de {zip_path.name}...")
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if readme_path.exists():
            zinfo = zipfile.ZipInfo("LISEZMOI_INSTALLATION.txt")
            zinfo.external_attr = 0o100644 << 16
            zf.writestr(zinfo, readme_path.read_bytes())

        for root, dirs, files in os.walk(app_path):
            rel_root = Path(root).relative_to(app_path.parent)

            for d in dirs:
                dir_path = rel_root / d
                d_str = str(dir_path).replace("\\", "/") + "/"
                zinfo = zipfile.ZipInfo(d_str)
                zinfo.external_attr = (0o040755 << 16) | 0x10
                zf.writestr(zinfo, "")

            for f in files:
                file_path = Path(root) / f
                rel_file = rel_root / f
                f_str = str(rel_file).replace("\\", "/")
                is_exec = f == "KINO" or file_path.suffix in (".sh", ".command")
                mode = 0o100755 if is_exec else 0o100644

                zinfo = zipfile.ZipInfo(f_str)
                zinfo.external_attr = mode << 16
                zf.writestr(zinfo, file_path.read_bytes())

    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f" [OK] {zip_path.name} créé ({zip_size_mb:.2f} Mo).")


def generate_native_mac_builder():
    """Génère un script shell que l'utilisateur peut lancer directement sur son Mac avec hdiutil."""
    script_sh = """#!/bin/bash
# ==============================================================================
# Script de génération native Apple Disk Image (.dmg) via hdiutil
# À exécuter directement sur macOS dans le dossier KINO :
#   bash dist/creer_dmg_natif_mac.sh
# ==============================================================================

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "🍏 Création du DMG macOS compressé via hdiutil natif..."

OUTPUT_DMG="dist/KINO_Apple_Natif.dmg"
SRC_FOLDER="dist/KINO.app"

if [ ! -d "$SRC_FOLDER" ]; then
    echo "❌ Le dossier $SRC_FOLDER n'existe pas. Exécutez d'abord creer_dmg_mac.py."
    exit 1
fi

rm -f "$OUTPUT_DMG"

hdiutil create \\
    -volname "KINO" \\
    -srcfolder "$SRC_FOLDER" \\
    -ov \\
    -format UDZO \\
    "$OUTPUT_DMG"

echo "✅ DMG Apple natif créé avec succès dans : $OUTPUT_DMG"
"""
    script_path = DIST_DIR / "creer_dmg_natif_mac.sh"
    script_path.write_text(script_sh, encoding="utf-8")
    return script_path


def main():
    print("================================================================")
    print("       PACKAGING MACOS KINO (APP BUNDLE, DMG & ZIP)             ")
    print("================================================================")
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Créer le bundle KINO.app dans dist/
    build_app_bundle(DIST_APP_DIR)

    # 2. Synchroniser vers KINO.app à la racine du projet
    if ROOT_APP_DIR.exists():
        shutil.rmtree(ROOT_APP_DIR)
    shutil.copytree(DIST_APP_DIR, ROOT_APP_DIR)
    print(f" [OK] Bundle racine {ROOT_APP_DIR} synchronisé.")

    # 3. Générer les instructions d'installation
    readme_path = generate_instructions()

    # 4. Générer le fichier KINO.dmg
    build_dmg(DIST_APP_DIR, readme_path)

    # 5. Générer le fichier KINO-macOS.zip
    build_zip(DIST_APP_DIR, readme_path)

    # 6. Générer le script shell macOS hdiutil
    generate_native_mac_builder()

    print("\n================================================================")
    print(" [TERMINE] Distribution macOS prête :")
    print(f"  1. DMG montable macOS  : {DIST_DIR / 'KINO.dmg'}")
    print(f"  2. Archive Zip macOS   : {DIST_DIR / 'KINO-macOS.zip'}")
    print(f"  3. Bundle KINO.app     : {DIST_DIR / 'KINO.app'}")
    print(f"  4. Guide d'installation: {readme_path}")
    print("================================================================")


if __name__ == "__main__":
    main()
