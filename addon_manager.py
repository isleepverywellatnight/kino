"""
KINO Add-on & Community Scraper Manager
========================================
Architecture modulaire et extensible permettant d'ajouter dynamiquement des
scrapers et extensions communautaires (fichiers .kino, JSON, plugins Python).
- Découverte automatique des addons dans ./addons et ~/.kino/addons
- Exécution isolée et multithreadée
- Activation / Désactivation en 1 clic
- Zéro modification du cœur de l'application
"""

import importlib.util
import json
import logging
import os
import shutil
import threading
import zipfile
from pathlib import Path
from typing import Dict, Any, List, Optional

import kino_db

logger = logging.getLogger("kino.addons")

APP_DIR = Path(__file__).resolve().parent
LOCAL_ADDONS_DIR = APP_DIR / "addons"
USER_ADDONS_DIR = Path.home() / ".kino" / "addons"

_ADDON_LOCK = threading.RLock()


def ensure_addon_dirs():
    LOCAL_ADDONS_DIR.mkdir(parents=True, exist_ok=True)
    USER_ADDONS_DIR.mkdir(parents=True, exist_ok=True)


def list_addons() -> List[Dict[str, Any]]:
    """Découvre tous les add-ons installés dans le projet et dans ~/.kino/addons/."""
    ensure_addon_dirs()
    addons = []
    seen_ids = set()

    # Charger les préférences d'activation enregistrées
    enabled_cfg = kino_db.get_config("addons_enabled", {})

    for search_dir in (USER_ADDONS_DIR, LOCAL_ADDONS_DIR):
        if not search_dir.is_dir():
            continue
        for folder in sorted(search_dir.iterdir()):
            if not folder.is_dir():
                continue
            manifest_file = folder / "addon.json"
            if manifest_file.exists():
                try:
                    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                    aid = manifest.get("id")
                    if aid and aid not in seen_ids:
                        seen_ids.add(aid)
                        # État activé par défaut ou selon config
                        is_enabled = enabled_cfg.get(aid, manifest.get("enabled", True))
                        manifest["enabled"] = bool(is_enabled)
                        manifest["path"] = str(folder)
                        manifest["is_user"] = (search_dir == USER_ADDONS_DIR)
                        addons.append(manifest)
                except Exception as e:
                    logger.warning(f"Impossible de charger l'addon {folder.name}: {e}")

    return addons


def toggle_addon(addon_id: str, enabled: bool) -> bool:
    """Active ou désactive un add-on spécifique."""
    with _ADDON_LOCK:
        enabled_cfg = kino_db.get_config("addons_enabled", {})
        enabled_cfg[addon_id] = bool(enabled)
        kino_db.save_config("addons_enabled", enabled_cfg)
        return True


def install_addon(zip_path_or_bytes) -> Dict[str, Any]:
    """Installe un addon depuis un fichier zip ou .kino dans ~/.kino/addons/."""
    ensure_addon_dirs()
    try:
        import io
        if isinstance(zip_path_or_bytes, (bytes, bytearray)):
            zf = zipfile.ZipFile(io.BytesIO(zip_path_or_bytes))
        else:
            zf = zipfile.ZipFile(zip_path_or_bytes)

        # Lire le manifest
        manifest_data = None
        manifest_arcname = None
        for name in zf.namelist():
            if name.endswith("addon.json"):
                manifest_data = json.loads(zf.read(name).decode("utf-8"))
                manifest_arcname = name
                break

        if not manifest_data or not manifest_data.get("id"):
            return {"status": "error", "message": "Fichier addon.json invalide ou absent du package."}

        addon_id = manifest_data["id"]
        target_dir = USER_ADDONS_DIR / addon_id
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        prefix = ""
        if manifest_arcname and "/" in manifest_arcname:
            prefix = manifest_arcname.rsplit("addon.json", 1)[0]

        for member in zf.infolist():
            arcname = member.filename
            if prefix and arcname.startswith(prefix):
                rel_name = arcname[len(prefix):]
            else:
                rel_name = arcname
            if not rel_name or rel_name.endswith("/"):
                continue
            dest_file = target_dir / rel_name
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            dest_file.write_bytes(zf.read(member))

        return {"status": "ok", "addon_id": addon_id, "manifest": manifest_data}
    except Exception as e:
        logger.error(f"Erreur installation addon: {e}")
        return {"status": "error", "message": str(e)}


def _load_addon_module(addon_path: Path, script_name: str = "scraper.py"):
    script_file = addon_path / script_name
    if not script_file.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(f"kino_addon_{addon_path.name}", script_file)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    except Exception as e:
        logger.error(f"Erreur chargement script {script_file}: {e}")
    return None


def run_addons_search(query: str, media_type: str = "movie", year: Optional[str] = None, season: Optional[int] = None, episode: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Exécute en parallèle tous les scrapers communautaires activés et agrège leurs résultats.
    """
    addons = list_addons()
    active_scrapers = [a for a in addons if a.get("enabled") and a.get("type") == "scraper"]
    if not active_scrapers:
        return []

    results = []
    threads = []
    results_lock = threading.Lock()

    def _worker(addon_info):
        mod = _load_addon_module(Path(addon_info["path"]), addon_info.get("entry_point", "scraper.py"))
        if mod and hasattr(mod, "search"):
            try:
                items = mod.search(query=query, media_type=media_type, year=year, season=season, episode=episode)
                if isinstance(items, list):
                    source_name = addon_info.get("name", "Addon")
                    for item in items:
                        if isinstance(item, dict):
                            item.setdefault("source", source_name)
                            item.setdefault("addon_id", addon_info.get("id"))
                            with results_lock:
                                results.append(item)
            except Exception as e:
                logger.debug(f"Erreur scraper {addon_info.get('id')}: {e}")

    for a in active_scrapers:
        t = threading.Thread(target=_worker, args=(a,), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=3.5)

    return results
