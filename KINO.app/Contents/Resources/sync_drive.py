#!/usr/bin/env python3
"""
KINO Google Drive Real-Time Auto-Synchronizer
Synchronisation automatique et transparente entre le poste local et Google Drive (Mac/Windows).

Modes d'utilisation :
1. Ponctuel : python sync_drive.py
2. Surveillance temps réel (daemon) : python sync_drive.py --watch
3. Hook Git automatique : appele apres chaque 'git commit'
"""

import os
import sys
import time
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Detection du dossier cible Google Drive
def get_gdrive_dir():
    import kino_db
    d = kino_db.find_gdrive_sync_dir()
    if d and d.is_dir():
        return d
    for win_drive in ("G:", "H:", "F:"):
        for sub in ("Mon Drive", "My Drive"):
            cand = Path(f"{win_drive}\\{sub}\\KINO")
            if cand.is_dir():
                return cand
    for sub in ("Google Drive\\Mon Drive\\KINO", "Google Drive\\My Drive\\KINO", "Google Drive\\KINO"):
        cand = Path.home() / sub
        if cand.is_dir():
            return cand
    return None


def run_sync(silent=False):
    target = get_gdrive_dir()
    if not target:
        if not silent:
            print("[SYNC DRIVE] Google Drive KINO non detecte sur ce poste.")
        return False

    if not silent:
        print(f"[SYNC DRIVE] Synchronisation vers : {target}")

    # 1. Synchronisation des fichiers sources, scripts et web
    if sys.platform == "win32":
        cmd = [
            "robocopy",
            str(BASE_DIR),
            str(target),
            "/E",
            "/XD", ".git", ".agents", ".gemini", "build", ".vscode", "__pycache__",
            "/XF", "*.pyc", "desktop.ini", "*.log",
            "/R:1",
            "/W:1",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS"
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            if not silent:
                print(f"[SYNC DRIVE] Erreur robocopy : {e}")
    else:
        # macOS / Linux rsync
        cmd = [
            "rsync",
            "-a",
            "--delete",
            "--exclude=.git",
            "--exclude=.agents",
            "--exclude=__pycache__",
            "--exclude=*.pyc",
            f"{BASE_DIR}/",
            f"{target}/"
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            if not silent:
                print(f"[SYNC DRIVE] Erreur rsync : {e}")

    # 2. Synchronisation de la base SQLite vers kino_sync.json
    try:
        import kino_db
        res = kino_db.db_sync_gdrive()
        if not silent and res.get("status") == "synced":
            print(f"[SYNC DRIVE] Base KINO synchronisee ({res.get('watchlist_count', 0)} watchlist, {res.get('history_count', 0)} historique).")
    except Exception as e:
        if not silent:
            print(f"[SYNC DRIVE] Avertissement sync BD : {e}")

    if not silent:
        print(f"[SYNC DRIVE] Synchronisation terminee avec succes a {time.strftime('%H:%M:%S')}.")
    return True


_WATCHER_RUNNING = False

def watch_and_sync(poll_interval=2.0, verbose=True):
    """Surveille les modifications de fichiers en temps reel et synchronise immediatement."""
    global _WATCHER_RUNNING
    if _WATCHER_RUNNING:
        return
    _WATCHER_RUNNING = True

    target = get_gdrive_dir()
    if not target:
        if verbose:
            print("[SYNC DRIVE] Google Drive KINO introuvable. Surveillance inactive.")
        return

    if verbose:
        print("=" * 65)
        print("   KINO DRIVE LIVE SYNC (SURVEILLANCE TEMPS REEL ACTIVE)")
        print(f"   Dossier source : {BASE_DIR}")
        print(f"   Dossier Drive  : {target}")
        print("   Chaque modification locale est automatiquement propagee sur Drive.")
        print("=" * 65)

    # Premiere synchronisation au demarrage
    run_sync(silent=not verbose)

    def get_snapshot():
        snap = {}
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if d not in (".git", ".agents", ".gemini", "build", "__pycache__", ".vscode")]
            for f in files:
                if f.endswith((".pyc", ".log")) or f == "desktop.ini":
                    continue
                fp = os.path.join(root, f)
                try:
                    snap[fp] = os.path.getmtime(fp)
                except OSError:
                    pass
        return snap

    last_snap = get_snapshot()

    try:
        while True:
            time.sleep(poll_interval)
            current_snap = get_snapshot()
            changed = False

            if set(current_snap.keys()) != set(last_snap.keys()):
                changed = True
            else:
                for k, mt in current_snap.items():
                    if mt != last_snap.get(k):
                        changed = True
                        break

            if changed:
                time.sleep(0.5)
                last_snap = get_snapshot()
                print(f"[{time.strftime('%H:%M:%S')}] [AUTO-SYNC DRIVE] Modification detectee, synchronisation en cours...")
                run_sync(silent=True)
    except KeyboardInterrupt:
        if verbose:
            print("\n[SYNC DRIVE] Surveillance arretee.")
    finally:
        _WATCHER_RUNNING = False


if __name__ == "__main__":
    if "--watch" in sys.argv or "-w" in sys.argv:
        watch_and_sync(verbose=True)
    else:
        run_sync(silent=False)
