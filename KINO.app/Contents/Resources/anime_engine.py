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

import datetime
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from typing import Dict, Any, List, Optional

try:
    from kino_db import db_cache_get, db_cache_set
except ImportError:
    def db_cache_get(k): return None
    def db_cache_set(k, v, ttl_sec=1800): pass

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


FALLBACK_ANIMES: List[Dict[str, Any]] = [
    {
        "kitsu_id": "7442",
        "id": "7442",
        "title": "Attack on Titan",
        "canonical_title": "Attack on Titan",
        "title_jp": "進撃の巨人",
        "title_romaji": "Shingeki no Kyojin",
        "synopsis": "Dans un monde où les humains vivent enfermés dans des cités entourées de gigantesques remparts pour se protéger de créatures colossales nommées Titans, le jeune Eren Jaeger jure d'éradiquer ces prédateurs.",
        "rating": "8.5",
        "score": "8.5",
        "episode_count": 25,
        "status": "finished",
        "show_type": "TV",
        "year": "2013",
        "poster": "https://media.kitsu.app/anime/poster_images/7442/medium.jpg",
        "cover": "https://media.kitsu.app/anime/cover_images/7442/large.jpg"
    },
    {
        "kitsu_id": "42765",
        "id": "42765",
        "title": "Jujutsu Kaisen",
        "canonical_title": "Jujutsu Kaisen",
        "title_jp": "呪術廻戦",
        "title_romaji": "Jujutsu Kaisen",
        "synopsis": "Yuji Itadori, lycéen aux aptitudes physiques exceptionnelles, avale une relique maudite de rang S pour sauver ses amis et se retrouve possédé par Ryomen Sukuna, le Roi des Fléaux.",
        "rating": "8.6",
        "score": "8.6",
        "episode_count": 24,
        "status": "finished",
        "show_type": "TV",
        "year": "2020",
        "poster": "https://media.kitsu.app/anime/poster_images/42765/medium.jpg",
        "cover": "https://media.kitsu.app/anime/cover_images/42765/large.jpg"
    },
    {
        "kitsu_id": "41370",
        "id": "41370",
        "title": "Demon Slayer: Kimetsu no Yaiba",
        "canonical_title": "Demon Slayer: Kimetsu no Yaiba",
        "title_jp": "鬼滅の刃",
        "title_romaji": "Kimetsu no Yaiba",
        "synopsis": "Après le massacre de sa famille par un démon et la transformation de sa jeune sœur Nezuko, Tanjiro Kamado devient pourfendeur de démons pour la délivrer de cette malédiction.",
        "rating": "8.5",
        "score": "8.5",
        "episode_count": 26,
        "status": "finished",
        "show_type": "TV",
        "year": "2019",
        "poster": "https://media.kitsu.app/anime/poster_images/41370/medium.jpg",
        "cover": "https://media.kitsu.app/anime/cover_images/41370/large.jpg"
    },
    {
        "kitsu_id": "1376",
        "id": "1376",
        "title": "Death Note",
        "canonical_title": "Death Note",
        "title_jp": "デスノート",
        "title_romaji": "Death Note",
        "synopsis": "Light Yagami, brillant lycéen, trouve un carnet surnaturel permettant de tuer quiconque dont on connaît le nom et le visage. Il entreprend d'éradiquer la criminalité sous le pseudonyme de Kira.",
        "rating": "8.7",
        "score": "8.7",
        "episode_count": 37,
        "status": "finished",
        "show_type": "TV",
        "year": "2006",
        "poster": "https://media.kitsu.app/anime/poster_images/1376/medium.jpg",
        "cover": "https://media.kitsu.app/anime/cover_images/1376/large.jpg"
    },
    {
        "kitsu_id": "3936",
        "id": "3936",
        "title": "Fullmetal Alchemist: Brotherhood",
        "canonical_title": "Fullmetal Alchemist: Brotherhood",
        "title_jp": "鋼の錬金術師 FULLMETAL ALCHEMIST",
        "title_romaji": "Hagane no Renkinjutsushi: Brotherhood",
        "synopsis": "Edward et Alphonse Elric parcourent le monde à la recherche de la Pierre Philosophale pour restaurer leurs corps perdus lors d'une tentative désastreuse de transmutation humaine.",
        "rating": "9.0",
        "score": "9.0",
        "episode_count": 64,
        "status": "finished",
        "show_type": "TV",
        "year": "2009",
        "poster": "https://media.kitsu.app/anime/poster_images/3936/medium.jpg",
        "cover": "https://media.kitsu.app/anime/cover_images/3936/large.jpg"
    },
    {
        "kitsu_id": "46474",
        "id": "46474",
        "title": "Frieren: Beyond Journey's End",
        "canonical_title": "Frieren: Beyond Journey's End",
        "title_jp": "葬送のフリーレン",
        "title_romaji": "Sousou no Frieren",
        "synopsis": "Après la défaite du Roi Démon par le groupe de héros, l'elfe magicienne Frieren entame un nouveau voyage pour comprendre la valeur éphémère du temps et des liens humains.",
        "rating": "9.1",
        "score": "9.1",
        "episode_count": 28,
        "status": "finished",
        "show_type": "TV",
        "year": "2023",
        "poster": "https://media.kitsu.app/anime/poster_images/46474/medium.jpg",
        "cover": "https://media.kitsu.app/anime/cover_images/46474/large.jpg"
    },
    {
        "kitsu_id": "43608",
        "id": "43608",
        "title": "Chainsaw Man",
        "canonical_title": "Chainsaw Man",
        "title_jp": "チェンソーマン",
        "title_romaji": "Chainsaw Man",
        "synopsis": "Denji, jeune homme criblé de dettes vivant avec son démon-tronçonneuse Pochita, fusionne avec ce dernier après avoir été trahi, devenant l'arme absolue de la Sécurité Publique.",
        "rating": "8.5",
        "score": "8.5",
        "episode_count": 12,
        "status": "finished",
        "show_type": "TV",
        "year": "2022",
        "poster": "https://media.kitsu.app/anime/poster_images/43608/medium.jpg",
        "cover": "https://media.kitsu.app/anime/cover_images/43608/large.jpg"
    },
    {
        "kitsu_id": "47098",
        "id": "47098",
        "title": "Solo Leveling",
        "canonical_title": "Solo Leveling",
        "title_jp": "俺だけレベルアップな件",
        "title_romaji": "Ore dake Level Up na Ken",
        "synopsis": "Sung Jinwoo, le chasseur de rang E le plus faible de toute l'humanité, reçoit la capacité unique d'évoluer sans limite via une interface de jeu invisible aux autres.",
        "rating": "8.4",
        "score": "8.4",
        "episode_count": 12,
        "status": "finished",
        "show_type": "TV",
        "year": "2024",
        "poster": "https://media.kitsu.app/anime/poster_images/47098/medium.jpg",
        "cover": "https://media.kitsu.app/anime/cover_images/47098/large.jpg"
    },
    {
        "kitsu_id": "12",
        "id": "12",
        "title": "One Piece",
        "canonical_title": "One Piece",
        "title_jp": "ONE PIECE",
        "title_romaji": "One Piece",
        "synopsis": "Monkey D. Luffy prend la mer à la recherche du trésor légendaire, le One Piece, avec l'ambition suprême de devenir le Roi des Pirates.",
        "rating": "8.6",
        "score": "8.6",
        "episode_count": 1100,
        "status": "current",
        "show_type": "TV",
        "year": "1999",
        "poster": "https://media.kitsu.app/anime/poster_images/12/medium.jpg",
        "cover": "https://media.kitsu.app/anime/cover_images/12/large.jpg"
    },
    {
        "kitsu_id": "6448",
        "id": "6448",
        "title": "Hunter x Hunter (2011)",
        "canonical_title": "Hunter x Hunter (2011)",
        "title_jp": "HUNTER×HUNTER（2011）",
        "title_romaji": "Hunter x Hunter (2011)",
        "synopsis": "Gon Freecss décide de passer le redoutable examen de Hunter dans l'espoir de retrouver son père Ging, l'un des Hunters les plus mystérieux et renommés au monde.",
        "rating": "8.9",
        "score": "8.9",
        "episode_count": 148,
        "status": "finished",
        "show_type": "TV",
        "year": "2011",
        "poster": "https://media.kitsu.app/anime/poster_images/6448/medium.jpg",
        "cover": "https://media.kitsu.app/anime/cover_images/6448/large.jpg"
    }
]


def fetch_kitsu_anime(endpoint: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    safe_params = {}
    if params:
        for k, v in params.items():
            if k == "page[limit]":
                safe_params[k] = min(max(1, int(v)), 20)
            else:
                safe_params[k] = v
    query_str = urllib.parse.urlencode(safe_params, safe="[]:") if safe_params else ""
    url = f"{KITSU_API}/{endpoint}?{query_str}" if query_str else f"{KITSU_API}/{endpoint}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=7) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = []
            for item in data.get("data", []):
                attr = item.get("attributes", {})
                titles = attr.get("titles", {})
                canonical = attr.get("canonicalTitle") or titles.get("en") or titles.get("en_jp") or ""
                poster = (attr.get("posterImage") or {}).get("medium") or (attr.get("posterImage") or {}).get("original") or ""
                avg = attr.get("averageRating")
                score_str = f"{float(avg)/10:.1f}" if avg else ""

                results.append({
                    "kitsu_id": item.get("id"),
                    "id": item.get("id"),
                    "title": canonical,
                    "canonical_title": canonical,
                    "title_jp": titles.get("ja_jp", ""),
                    "title_romaji": titles.get("en_jp", ""),
                    "synopsis": attr.get("synopsis", ""),
                    "rating": score_str,
                    "score": score_str,
                    "episode_count": attr.get("episodeCount") or 0,
                    "status": attr.get("status"),
                    "show_type": attr.get("showType"),
                    "year": (attr.get("startDate") or "")[:4],
                    "poster": poster,
                    "cover": (attr.get("coverImage") or {}).get("large") or "",
                })
            return results if results else []
    except Exception as e:
        logger.error(f"Erreur Kitsu API ({url}): {e}")
        return []


def get_trending_anime() -> List[Dict[str, Any]]:
    """Récupère les animes tendance du moment."""
    res = fetch_kitsu_anime("trending/anime")
    return res if res else list(FALLBACK_ANIMES)


def get_popular_anime(limit: int = 20) -> List[Dict[str, Any]]:
    """Récupère les animes les plus populaires de tous les temps."""
    clamped_limit = min(max(1, limit), 20)
    res = fetch_kitsu_anime("anime", {
        "sort": "-userCount",
        "page[limit]": clamped_limit,
    })
    return res if res else list(FALLBACK_ANIMES)


def search_anime(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Recherche un anime par titre (français, anglais ou romaji)."""
    clamped_limit = min(max(1, limit), 20)
    res = fetch_kitsu_anime("anime", {
        "filter[text]": query,
        "page[limit]": clamped_limit,
    })
    if not res:
        q_lower = query.lower()
        matched = [a for a in FALLBACK_ANIMES if q_lower in a["title"].lower() or q_lower in a.get("title_romaji", "").lower()]
        return matched if matched else []
    return res


def get_airing_schedule() -> Dict[str, Any]:
    """
    Récupère le calendrier de diffusion Simulcast de la semaine via AniList GraphQL.
    Organise les épisodes par jour (Aujourd'hui, Demain, etc.) avec compte à rebours.
    """
    cache_key = "anime_simulcast_schedule_v2"
    cached = db_cache_get(cache_key)
    if cached:
        return cached

    now = int(time.time())
    start_time = now - 86400  # Les dernières 24h
    end_time = now + 86400 * 6  # Les 6 prochains jours

    query = """
    query ($start: Int, $end: Int) {
      Page(page: 1, perPage: 50) {
        airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {
          id
          airingAt
          episode
          timeUntilAiring
          media {
            id
            idMal
            title {
              romaji
              english
              native
            }
            coverImage {
              medium
              large
            }
            bannerImage
            format
            genres
            averageScore
          }
        }
      }
    }
    """

    req = urllib.request.Request(
        "https://graphql.anilist.co",
        data=json.dumps({"query": query, "variables": {"start": start_time, "end": end_time}}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "KINO-Desktop/2.0"},
    )

    days_map: Dict[str, List[Dict[str, Any]]] = {}
    day_names_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    try:
        with urllib.request.urlopen(req, timeout=5.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            schedules = data.get("data", {}).get("Page", {}).get("airingSchedules", [])

            now_dt = datetime.datetime.now()
            today_date = now_dt.date()

            for item in schedules:
                media = item.get("media") or {}
                fmt = media.get("format")
                if fmt not in ("TV", "ONA", "TV_SHORT"):
                    continue

                titles = media.get("title") or {}
                canonical = titles.get("english") or titles.get("romaji") or titles.get("native") or "Anime"
                airing_at = item.get("airingAt", 0)
                time_until = item.get("timeUntilAiring", 0)

                dt = datetime.datetime.fromtimestamp(airing_at)
                item_date = dt.date()

                delta_days = (item_date - today_date).days
                if delta_days == 0:
                    day_label = "Aujourd'hui"
                elif delta_days == 1:
                    day_label = "Demain"
                elif 0 < delta_days < 7:
                    day_label = day_names_fr[dt.weekday()]
                elif delta_days < 0:
                    day_label = "Hier"
                else:
                    day_label = day_names_fr[dt.weekday()]

                cover = (media.get("coverImage") or {}).get("large") or (media.get("coverImage") or {}).get("medium") or ""
                score = media.get("averageScore")
                score_str = f"{float(score)/10:.1f}" if score else ""

                entry = {
                    "id": media.get("id"),
                    "id_mal": media.get("idMal"),
                    "title": canonical,
                    "title_romaji": titles.get("romaji", ""),
                    "title_native": titles.get("native", ""),
                    "episode": item.get("episode", 1),
                    "airing_at": airing_at,
                    "time_until": time_until,
                    "is_available": time_until <= 0,
                    "poster": cover,
                    "banner": media.get("bannerImage") or "",
                    "genres": (media.get("genres") or [])[:3],
                    "score": score_str,
                    "time_str": dt.strftime("%H:%M"),
                }

                if day_label not in days_map:
                    days_map[day_label] = []
                days_map[day_label].append(entry)

            result = {
                "ok": True,
                "days": days_map,
                "updated_at": now,
            }
            db_cache_set(cache_key, result, ttl_sec=1800)
            return result
    except Exception as e:
        logger.error(f"Erreur AniList AiringSchedule: {e}")
        return {"ok": False, "days": {}, "error": str(e)}

