"""
KINO Intro Engine — Calculateur Intelligent de Skip Intro & Outro
=================================================================
Détection ultra-précise des génériques (Opening / Ending / Prologue / Récap) :
1. Base collaborative spécialisée AniSkip (API v2) pour les animés japonais.
2. Extraction en temps réel des chapitres intégrés (MKV / MP4) via ffprobe.
3. Calculateur heuristique adaptatif (90s anime / 60s série) avec interface de secours.
4. Cache persistant SQLite haute performance pour des réponses instantanées (< 1ms).
"""

import json
import logging
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional, List

try:
    from kino_db import db_cache_get, db_cache_set
except ImportError:
    # Fallback si standalone
    _MEM_CACHE = {}
    def db_cache_get(k): return _MEM_CACHE.get(k)
    def db_cache_set(k, v, ttl_sec=86400): _MEM_CACHE[k] = v

logger = logging.getLogger("kino.intro")


def find_ffprobe() -> Optional[str]:
    """Détecte l'exécutable FFprobe sur la machine (WinGet, PATH, Program Files, macOS, Linux)."""
    path = shutil.which("ffprobe")
    if path and os.path.exists(path):
        return path
    candidates = [
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffprobe.exe"),
        r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
        r"C:\ffmpeg\bin\ffprobe.exe",
        "/opt/homebrew/bin/ffprobe",
        "/usr/local/bin/ffprobe",
        "/usr/bin/ffprobe",
    ]
    wg_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if wg_dir.exists():
        for p in wg_dir.glob("**/ffprobe.exe"):
            if p.is_file():
                candidates.append(str(p))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def clean_series_name(title: str) -> str:
    """Nettoie le nom de la série pour la recherche d'identifiants externes."""
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", "", title)
    s = re.sub(r"\b(?:s\d+|season\s*\d+|part\s*\d+)\b.*", "", s, flags=re.IGNORECASE)
    s = s.replace(".", " ").replace("-", " ").strip()
    return re.sub(r"\s+", " ", s)


def resolve_mal_id(title: str, season: Optional[int] = 1) -> Optional[int]:
    """
    Résout l'identifiant MyAnimeList (MAL ID) via l'API Kitsu et ses mappings externes.
    Utilise le cache SQLite local (durée 30 jours).
    """
    clean_t = clean_series_name(title)
    season_num = int(season or 1)
    
    # Clé de cache
    cache_key = f"mal_id:{clean_t.lower()}:s{season_num}"
    cached = db_cache_get(cache_key)
    if cached is not None:
        return cached

    search_queries = []
    if season_num > 1:
        search_queries.append(f"{clean_t} Season {season_num}")
        search_queries.append(f"{clean_t} {season_num}")
    search_queries.append(clean_t)

    mal_id = None
    for q in search_queries:
        try:
            url = f"https://kitsu.io/api/edge/anime?filter[text]={urllib.parse.quote(q)}&page[limit]=3"
            req = urllib.request.Request(url, headers={"User-Agent": "KINO-Desktop/2.0 (IntroEngine)"})
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                anime_list = data.get("data", [])
                if not anime_list:
                    continue

                # Pour chaque résultat Kitsu, chercher le mapping myanimelist
                for item in anime_list:
                    kitsu_id = item.get("id")
                    if not kitsu_id:
                        continue
                    
                    map_url = f"https://kitsu.io/api/edge/anime/{kitsu_id}/mappings"
                    map_req = urllib.request.Request(map_url, headers={"User-Agent": "KINO-Desktop/2.0 (IntroEngine)"})
                    try:
                        with urllib.request.urlopen(map_req, timeout=3.5) as m_resp:
                            m_data = json.loads(m_resp.read().decode("utf-8"))
                            for mapping in m_data.get("data", []):
                                attr = mapping.get("attributes", {})
                                if attr.get("externalSite") == "myanimelist/anime":
                                    ext_id = attr.get("externalId")
                                    if ext_id and str(ext_id).isdigit():
                                        mal_id = int(ext_id)
                                        break
                    except Exception:
                        pass
                    if mal_id:
                        break
        except Exception as e:
            logger.debug(f"Erreur recherche Kitsu ({q}): {e}")
        
        if mal_id:
            break

    if mal_id:
        db_cache_set(cache_key, mal_id, ttl_sec=86400 * 30)
    return mal_id


def query_aniskip(mal_id: int, episode: int) -> Optional[Dict[str, Any]]:
    """
    Interroge l'API officielle AniSkip pour obtenir les timestamps d'intro (OP), d'ending (ED) et de récap.
    """
    url = f"https://api.aniskip.com/v2/skip-times/{mal_id}/{episode}?types[]=op&types[]=ed&types[]=recap&types[]=mixed-op&types[]=mixed-ed&episodeLength=0"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 KINO-Desktop/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=4.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("found") or not data.get("results"):
                return None

            op_interval = None
            ed_interval = None
            recap_interval = None

            for item in data.get("results", []):
                stype = (item.get("skipType") or "").lower()
                interval = item.get("interval", {})
                start = float(interval.get("startTime", 0))
                end = float(interval.get("endTime", 0))

                if stype in ("op", "mixed-op"):
                    op_interval = {"start": round(start, 2), "end": round(end, 2)}
                elif stype in ("ed", "mixed-ed"):
                    ed_interval = {"start": round(start, 2), "end": round(end, 2)}
                elif stype == "recap":
                    recap_interval = {"start": round(start, 2), "end": round(end, 2)}

            if op_interval or ed_interval or recap_interval:
                return {
                    "found": True,
                    "source": "aniskip",
                    "provider": "AniSkip (Communautaire)",
                    "op": op_interval,
                    "ed": ed_interval,
                    "recap": recap_interval,
                    "default_skip": 90.0,
                }
    except Exception as e:
        logger.debug(f"AniSkip query error: {e}")
    return None


def extract_chapters_from_stream(stream_url: str, timeout_sec: float = 4.0) -> List[Dict[str, Any]]:
    """
    Extrait les chapitres d'un flux MKV / MP4 distant à la volée via ffprobe
    sans télécharger la vidéo grâce aux requêtes HTTP partielles (Range).
    """
    ffprobe = find_ffprobe()
    if not ffprobe or not stream_url:
        return []

    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_chapters",
        stream_url,
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_sec,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout)
            return data.get("chapters", [])
    except Exception as e:
        logger.debug(f"ffprobe chapters extraction error: {e}")
    return []


def parse_video_chapters(chapters: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyse les métadonnées de chapitres pour détecter l'Opening, le Récap et l'Ending."""
    op = None
    ed = None
    recap = None

    for ch in chapters:
        tags = ch.get("tags", {})
        title = tags.get("title", "").strip().lower()
        start = float(ch.get("start_time", 0.0))
        end = float(ch.get("end_time", 0.0))
        duration = end - start

        # Opening / Intro
        if re.search(r"\b(intro|opening|theme|op|title sequence|générique)\b", title, re.IGNORECASE) and not re.search(r"\b(ending|fin|credits)\b", title, re.IGNORECASE):
            if 15.0 <= duration <= 210.0 and start < 600.0:
                op = {"start": round(start, 2), "end": round(end, 2)}
        # Prologue / Cold Open / Recap
        elif re.search(r"\b(recap|prologue|cold open|prélude|previously)\b", title, re.IGNORECASE):
            if start < 300.0:
                recap = {"start": round(start, 2), "end": round(end, 2)}
        # Ending / Credits
        elif re.search(r"\b(ending|ed|credits|générique fin|end credits)\b", title, re.IGNORECASE):
            if start > 300.0:
                ed = {"start": round(start, 2), "end": round(end, 2)}

    # Détection heuristique sur chapitres non nommés (ex: Chapter 01, Chapter 02)
    if not op and len(chapters) >= 3:
        for idx, ch in enumerate(chapters[:4]):
            start = float(ch.get("start_time", 0.0))
            end = float(ch.get("end_time", 0.0))
            duration = end - start
            # Un chapitre calibré entre 80s et 95s (format générique standard) dans les 5 premières minutes
            if 78.0 <= duration <= 95.0 and start < 360.0:
                op = {"start": round(start, 2), "end": round(end, 2)}
                if idx > 0:
                    prev_ch = chapters[idx - 1]
                    recap = {
                        "start": round(float(prev_ch.get("start_time", 0.0)), 2),
                        "end": round(float(prev_ch.get("end_time", 0.0)), 2),
                    }
                break

    return {"op": op, "ed": ed, "recap": recap}


def get_intro_timestamps(
    title: str,
    media_type: str = "series",
    season: Optional[int] = 1,
    episode: Optional[int] = 1,
    stream_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Point d'entrée principal : résout les timestamps de l'intro et de l'outro.
    Stratégie hybride en 3 étapes :
    1. Base AniSkip (animés)
    2. Extraction des chapitres du conteneur vidéo (MKV / MP4)
    3. Calculateur adaptatif (90s anime / 60s série)
    """
    clean_t = clean_series_name(title)
    s_num = int(season or 1)
    e_num = int(episode or 1)
    is_anime = (media_type == "anime") or ("anime" in str(title).lower())

    cache_key = f"intro_v2:{clean_t.lower()}:s{s_num}:e{e_num}"
    cached = db_cache_get(cache_key)
    if cached and cached.get("found"):
        return cached

    # 1. AniSkip pour les animés (ou tentative sur toute série si identifiée anime)
    mal_id = None
    if is_anime or s_num <= 10:
        mal_id = resolve_mal_id(clean_t, season=s_num)
        if mal_id:
            aniskip_res = query_aniskip(mal_id, e_num)
            if aniskip_res:
                db_cache_set(cache_key, aniskip_res, ttl_sec=86400 * 14)
                return aniskip_res

    # 2. Extraction des chapitres intégrés au fichier
    if stream_url:
        try:
            chapters = extract_chapters_from_stream(stream_url, timeout_sec=3.8)
            if chapters:
                parsed_ch = parse_video_chapters(chapters)
                if parsed_ch.get("op") or parsed_ch.get("ed"):
                    res = {
                        "found": True,
                        "source": "chapters",
                        "provider": "Chapitres intégrés (MKV/MP4)",
                        "op": parsed_ch.get("op"),
                        "ed": parsed_ch.get("ed"),
                        "recap": parsed_ch.get("recap"),
                        "default_skip": 90.0 if is_anime else 60.0,
                    }
                    db_cache_set(cache_key, res, ttl_sec=86400 * 7)
                    return res
        except Exception as e:
            logger.debug(f"Erreur extraction chapitres: {e}")

    # 3. Fallback Heuristique Adaptatif
    fallback_skip = 90.0 if is_anime else 60.0
    return {
        "found": False,
        "source": "heuristic",
        "provider": "Calculateur adaptatif",
        "op": None,
        "ed": None,
        "recap": None,
        "default_skip": fallback_skip,
        "estimated_intro_start": 60.0 if is_anime else 45.0,
    }
