"""Stremio add-ons the user adds by their address: more sources, and catalogues.

An add-on is a web service that answers in JSON (the Stremio add-on protocol):
its manifest says what it offers, /stream/... lists sources for a title and
/catalog/... lists titles. KINO's own indexers already speak this protocol
(torrent_engine.indexer_sources); the user's add-ons join them. Nothing of the
add-on runs on this computer: KINO only reads its answers.
"""
import re
import threading
import urllib.parse

import kino_db
from config import http_json

STORE_KEY = "stremio_addons_v1"
MAX_ADDONS = 30
MAX_CATALOGS = 8
STREAM_TIMEOUT = 8
_LOCK = threading.Lock()
_TYPES = {"movie", "series"}


class AddonError(ValueError):
    """An address that is not a usable add-on; the message is shown as is."""


def _base_of(address):
    """The add-on's base address from what the user pasted (…/manifest.json, stremio://…)."""
    url = str(address or "").strip()
    if url.startswith("stremio://"):
        url = "https://" + url[len("stremio://"):]
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AddonError("Collez l'adresse d'un addon : elle finit par manifest.json.")
    path = parsed.path
    if path.endswith("/manifest.json"):
        path = path[: -len("/manifest.json")]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))


def _resource_names(manifest):
    names = set()
    for resource in manifest.get("resources") or []:
        name = resource.get("name") if isinstance(resource, dict) else resource
        if isinstance(name, str):
            names.add(name)
    return names


def _entry(base, manifest):
    """What KINO keeps of a manifest: enough to query the add-on, nothing else."""
    if not isinstance(manifest, dict) or not manifest.get("id") or not manifest.get("name"):
        raise AddonError("Cette adresse ne répond pas comme un addon.")
    resources = _resource_names(manifest) & {"stream", "catalog"}
    if not resources:
        raise AddonError("Cet addon ne propose ni sources ni catalogues : KINO ne peut pas s'en servir.")
    types = sorted(_TYPES & {str(t) for t in manifest.get("types") or []})
    catalogs = []
    for catalog in manifest.get("catalogs") or []:
        if not isinstance(catalog, dict) or catalog.get("type") not in _TYPES or not catalog.get("id"):
            continue
        # A catalogue that cannot be read without a search or a genre is left out.
        extra = [e for e in catalog.get("extra") or [] if isinstance(e, dict)]
        if any(e.get("isRequired") for e in extra) or "search" in (catalog.get("extraRequired") or []):
            continue
        catalogs.append({"type": catalog["type"], "id": str(catalog["id"]), "name": str(catalog.get("name") or manifest["name"])})
    prefixes = manifest.get("idPrefixes")
    return {
        "id": str(manifest["id"])[:120],
        "name": str(manifest["name"])[:80],
        "version": str(manifest.get("version") or "")[:20],
        "description": str(manifest.get("description") or "")[:300],
        "base": base,
        "streams": "stream" in resources,
        "types": types,
        # Sources only for titles KINO knows by their IMDb number (tt…).
        "imdb": not prefixes or any(str(p).startswith("tt") for p in prefixes),
        "catalogs": catalogs[:MAX_CATALOGS] if "catalog" in resources else [],
        "enabled": True,
    }


def installed():
    stored = kino_db.get_config(STORE_KEY, [])
    return [a for a in stored if isinstance(a, dict) and a.get("id") and a.get("base")] if isinstance(stored, list) else []


def _save(addons):
    kino_db.save_config(STORE_KEY, addons[:MAX_ADDONS])


def add(address, *, fetch=None):
    """Reads the manifest at this address and keeps the add-on; the same add-on is updated."""
    base = _base_of(address)
    fetch = fetch or http_json
    try:
        manifest = fetch(base + "/manifest.json", timeout=10, retries=1)
    except Exception as error:
        raise AddonError("Cet addon ne répond pas. Vérifiez l'adresse, puis réessayez.") from error
    entry = _entry(base, manifest)
    with _LOCK:
        addons = [a for a in installed() if a["id"] != entry["id"] and a["base"] != base]
        if len(addons) >= MAX_ADDONS:
            raise AddonError(f"KINO garde {MAX_ADDONS} addons au plus : retirez-en un d'abord.")
        addons.append(entry)
        _save(addons)
    return entry


def remove(addon_id):
    with _LOCK:
        addons = installed()
        kept = [a for a in addons if a["id"] != addon_id]
        if len(kept) == len(addons):
            raise AddonError("Addon inconnu.")
        _save(kept)


def set_enabled(addon_id, enabled):
    with _LOCK:
        addons = installed()
        for addon in addons:
            if addon["id"] == addon_id:
                addon["enabled"] = bool(enabled)
                _save(addons)
                return addon
    raise AddonError("Addon inconnu.")


def stream_sources(target, media_type):
    """[(bucket, url, label, timeout)] for torrent_engine.fetch_streams: the
    enabled add-ons that give sources for this kind of title."""
    kind = "movie" if media_type == "movie" else "series"
    if not re.fullmatch(r"tt\d{1,12}(?::\d{1,4}:\d{1,5})?", str(target or "")):
        return []
    sources = []
    for addon in installed():
        if addon.get("enabled") and addon.get("streams") and addon.get("imdb") and kind in (addon.get("types") or []):
            url = f"{addon['base']}/stream/{kind}/{urllib.parse.quote(target, safe=':')}.json"
            sources.append((f"stremio:{addon['id']}", url, addon["name"], STREAM_TIMEOUT))
    return sources


def catalogs():
    """Every catalogue of the enabled add-ons: {addon, name, type, id}."""
    found = []
    for addon in installed():
        if addon.get("enabled"):
            for catalog in addon.get("catalogs") or []:
                found.append({"addon": addon["id"], "addon_name": addon["name"], **catalog})
    return found


def catalog_items(addon_id, kind, catalog_id, *, fetch=None):
    """The titles of one catalogue that KINO can open (IMDb numbers)."""
    addon = next((a for a in installed() if a["id"] == addon_id and a.get("enabled")), None)
    if addon is None or not any(c["type"] == kind and c["id"] == catalog_id for c in addon.get("catalogs") or []):
        raise AddonError("Catalogue inconnu.")
    url = f"{addon['base']}/catalog/{kind}/{urllib.parse.quote(catalog_id, safe='')}.json"
    data = (fetch or http_json)(url, timeout=10, retries=1) or {}
    items = []
    for meta in data.get("metas") or []:
        if not isinstance(meta, dict) or not re.fullmatch(r"tt\d{1,12}", str(meta.get("id") or "")):
            continue
        items.append({
            "id": meta["id"], "type": kind, "name": str(meta.get("name") or "")[:200],
            "poster": str(meta.get("poster") or ""), "year": str(meta.get("releaseInfo") or meta.get("year") or "")[:12],
            "imdbRating": str(meta.get("imdbRating") or ""),
        })
    return items[:60]
