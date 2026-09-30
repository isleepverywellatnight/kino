"""
KINO Anime Engine (Kitsu / AniList & Filler Guide)
==================================================
Espace spécialisé pour l'animation japonaise :
- Découverte des tendances et animes populaires (API Kitsu)
- Titres originaux en Romaji, Japonais et Français
- Détecteur d'épisodes fillers (hors-séries vs canon)
- Recherche automatique prioritaire VOSTFR (japonais sous-titré français)
- Zéro clé API requise (100% public et gratuit)
"""

import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Dict, Any, List, Optional

logger = logging.getLogger("kino.anime")

KITSU_API = "https://kitsu.io/api/edge"
HEADERS = {
    "User-Agent": "KINO-Desktop/2.0 (AnimeEngine)",
    "Accept": "application/vnd.api+json",
}

# Base de données intégrée des fillers pour les animes les plus célèbres (plages d'épisodes hors-série)
KNOWN_FILLERS: Dict[str, List[tuple]] = {
    "naruto": [
        (26, 26), (97, 97), (101, 106), (136, 219)
    ],
    "naruto shippuden": [
        (28, 28), (57, 71), (91, 112), (144, 151), (170, 171), (176, 196),
        (223, 242), (257, 260), (271, 271), (279, 281), (284, 295), (303, 320),
        (347, 361), (376, 377), (388, 390), (394, 413), (416, 417), (422, 423),
        (427, 450), (464, 469), (480, 483)
    ],
    "bleach": [
        (33, 33), (50, 50), (64, 109), (128, 137), (147, 149), (168, 189),
        (204, 205), (213, 214), (228, 265), (287, 287), (298, 299), (303, 305),
        (311, 341), (355, 355)
    ],
    "one piece": [
        (54, 61), (98, 99), (102, 102), (131, 143), (196, 206), (220, 226),
        (279, 283), (291, 292), (303, 303), (317, 319), (326, 336), (382, 384),
        (406, 407), (426, 429), (457, 458), (492, 492), (542, 542), (575, 578),
        (590, 590), (626, 628), (747, 750), (775, 775), (780, 782), (807, 807),
        (881, 881), (895, 896), (907, 907), (1029, 1030)
    ],
    "boruto": [
        (16, 17), (40, 41), (48, 50), (67, 69), (96, 97), (112, 119), (138, 140),
        (152, 156), (231, 232), (256, 258)
    ],
    "dragon ball": [
        (29, 33), (45, 45), (79, 83), (127, 132), (149, 153)
    ],
    "dragon ball z": [
        (9, 10), (12, 17), (39, 44), (102, 102), (108, 117), (124, 125), (170, 171),
        (174, 174), (195, 199), (202, 204), (274, 274), (288, 288)
    ],
    "fairy tail": [
        (69, 75), (125, 150), (202, 226), (268, 268)
    ],
    "black clover": [
        (29, 29), (66, 66), (123, 125), (131, 131), (134, 135), (142, 148)
    ],
    "rurouni kenshin": [
        (13, 18), (22, 22), (25, 27), (63, 95)
    ],
}


def is_episode_filler(series_title: str, ep_number: int) -> Dict[str, Any]:
    """Détecte si un épisode est Canon (histoire originale) ou Filler (hors-série)."""
    clean_title = re.sub(r"[^a-zA-Z0-9\s]", "", series_title.lower()).strip()
    for key in sorted(KNOWN_FILLERS.keys(), key=len, reverse=True):
        ranges = KNOWN_FILLERS[key]
        if key in clean_title:
            for r_start, r_end in ranges:
                if r_start <= ep_number <= r_end:
                    return {
                        "is_filler": True,
                        "type": "Filler (Hors-série)",
                        "badge": "FILLER",
                        "description": "Épisode hors-série non présent dans le manga original."
                    }
            return {
                "is_filler": False,
                "type": "Canon",
                "badge": "CANON",
                "description": "Épisode fidèle à l'histoire originale du manga."
            }

    # Pour les animes saisonniers récents (ex: Jujutsu Kaisen, Demon Slayer), 99% sont 100% canon
    return {
        "is_filler": False,
        "type": "Canon",
        "badge": "CANON",
        "description": "Épisode canonique."
    }


def fetch_kitsu_anime(endpoint: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    query_str = urllib.parse.urlencode(params) if params else ""
    url = f"{KITSU_API}/{endpoint}?{query_str}" if query_str else f"{KITSU_API}/{endpoint}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = []
            for item in data.get("data", []):
                attr = item.get("attributes", {})
                titles = attr.get("titles", {})
                canonical = attr.get("canonicalTitle") or titles.get("en") or titles.get("en_jp") or ""
                poster = (attr.get("posterImage") or {}).get("medium") or (attr.get("posterImage") or {}).get("original") or ""

                results.append({
                    "kitsu_id": item.get("id"),
                    "title": canonical,
                    "title_jp": titles.get("ja_jp", ""),
                    "title_romaji": titles.get("en_jp", ""),
                    "synopsis": attr.get("synopsis", ""),
                    "rating": str(attr.get("averageRating") or ""),
                    "episode_count": attr.get("episodeCount") or 0,
                    "status": attr.get("status"),
                    "show_type": attr.get("showType"),
                    "year": (attr.get("startDate") or "")[:4],
                    "poster": poster,
                    "cover": (attr.get("coverImage") or {}).get("large") or "",
                })
            return results
    except Exception as e:
        logger.error(f"Erreur Kitsu API ({url}): {e}")
        return []


def get_trending_anime() -> List[Dict[str, Any]]:
    """Récupère les 10 animes les plus populaires du moment."""
    return fetch_kitsu_anime("trending/anime")


def get_popular_anime(limit: int = 24) -> List[Dict[str, Any]]:
    """Récupère les animes les plus populaires de tous les temps."""
    return fetch_kitsu_anime("anime", {
        "sort": "-userCount",
        "page[limit]": limit,
    })


def search_anime(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Recherche un anime par titre (français, anglais ou romaji)."""
    return fetch_kitsu_anime("anime", {
        "filter[text]": query,
        "page[limit]": limit,
    })
