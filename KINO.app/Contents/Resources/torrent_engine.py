"""
KINO Torrent Engine (Scraper, Parser, Anti-Fake Validator & Scoring d'Élite)
=============================================================================
Architecture modulaire haute performance pour l'agrégation et le filtrage des flux torrents :
- Détection linguistique fine (VFF / TrueFrench, VFQ québécois, MULTI, VOSTFR)
- Extraction technique ultra-précise (REMUX, BluRay, WEB-DL, HEVC/x265, AV1, Dolby Vision, Atmos)
- Filtre draconien anti-fake / anti-CAM (vérification stricte du débit binaire par rapport à la durée)
- Blacklist des groupes de releases polluées (bannières de casino 1xBet, watermarks de caméras)
- Moteur de scoring cinéphile et déduplication intelligente multi-sources
- Zéro dépendance externe (100% Python standard library)
"""

import base64
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request

# Constantes et badges de débridage instantané
INSTANT_BADGES = ("RD+", "AD+", "TB+", "DL+", "PM+", "OC+", "ED+")

# Blacklist de groupes ou tags de mauvaise qualité / fakes / pubs incrustées
TRASH_TAGS_REGEX = re.compile(
    r"\b("
    r"CAM|HDCAM|CAMRIP|TS|HDTS|TELESYNC|TELECINE|SCR|SCREENER|DVDSCREENER|WP|WORKPRINT|"
    r"1XBET|BETWINNER|LINEBET|MELBET|PARI|KORSUB|HC|SUBHC|HARDCODED|"
    r"SAMPLE|PROMO|TRAILER"
    r")\b",
    re.IGNORECASE,
)


def clean_meta_text(text: str) -> str:
    """Nettoie le texte des métadonnées pour éliminer les émojis superflus et le bruit visuel."""
    if not text:
        return ""
    text = re.sub(r"[👤👥]\s*(\d+)", r"\1 seeders", text)
    text = text.replace("💾", " • ").replace("⚙️", " • ").replace("📹", " • ").replace("🔎", " • ").replace("🏷️", " • ")
    text = text.replace("⭐", " • ").replace("📄", "").replace("🔊", " • ")
    # Supprimer les drapeaux émojis (Unicode regional indicators)
    text = re.sub(r"[\U0001F1E6-\U0001F1FF]{2}", "", text)
    # Nettoyer les puces multiples et espaces
    text = re.sub(r"\s*•\s*•\s*", " • ", text)
    text = re.sub(r"\s+", " ", text).strip(" •/")
    return text


def parse_release_details(title: str, meta: str = "") -> dict:
    """
    Analyse en profondeur le titre d'une release torrent et extrait toutes ses caractéristiques
    techniques, linguistiques et sonores de manière standardisée.
    """
    raw = f"{title} {meta}"
    t = raw.lower()

    # 1. Résolution
    resolution = "1080p"
    if re.search(r"\b(2160p|4k|uhd)\b", t):
        resolution = "4K"
    elif re.search(r"\b(1080p|fhd|1080i)\b", t):
        resolution = "1080p"
    elif re.search(r"\b(720p|hd)\b", t):
        resolution = "720p"
    elif re.search(r"\b(480p|576p|sd|dvdrip|dvd|xvid)\b", t):
        resolution = "SD"

    # 2. Source vidéo & Qualité d'encodage
    source = "WEB-DL"
    if re.search(r"\b(remux|bdremux|uhd[\.\-\s]?remux)\b", t):
        source = "REMUX"
    elif re.search(r"\b(bluray|bdrip|brrip|blu-ray)\b", t):
        source = "BluRay"
    elif re.search(r"\b(web-dl|webrip|web|amzn|nf|atvp|dsnp)\b", t):
        source = "WEB-DL"
    elif re.search(r"\b(hdtv|pdtv|dsr)\b", t):
        source = "HDTV"
    elif TRASH_TAGS_REGEX.search(raw):
        source = "CAM"

    # 3. Dynamic Range (HDR / Dolby Vision / SDR)
    has_dv = bool(re.search(r"\b(dv|dovi|dolby[\s\.\-]*vision)\b", t))
    has_hdr10plus = bool(re.search(r"\b(hdr10\+|hdr10plus)\b", t))
    has_hdr = bool(re.search(r"\b(hdr|hdr10|hlg)\b", t)) or has_hdr10plus

    hdr_type = "SDR"
    if has_dv and has_hdr:
        hdr_type = "DV HDR"
    elif has_dv:
        hdr_type = "DV"
    elif has_hdr10plus:
        hdr_type = "HDR10+"
    elif has_hdr:
        hdr_type = "HDR"

    # 4. Codec Vidéo
    codec = "x264"
    if re.search(r"\b(av1)\b", t):
        codec = "AV1"
    elif re.search(r"\b(hevc|x265|h\.?265|265)\b", t):
        codec = "HEVC"
    elif re.search(r"\b(x264|h\.?264|avc|264)\b", t):
        codec = "x264"

    # 5. Format Audio
    audio_formats = []
    if re.search(r"\b(atmos|dolby[\.\-\s]?atmos)\b", t):
        audio_formats.append("Atmos")
    if re.search(r"\b(truehd|true-hd)\b", t):
        audio_formats.append("TrueHD")
    if re.search(r"\b(dts[\.\-\s]?hd|dts[\.\-\s]?x|dts[\.\-\s]?ma)\b", t):
        audio_formats.append("DTS-HD")
    elif re.search(r"\b(dts)\b", t):
        audio_formats.append("DTS")
    if re.search(r"\b(e[\.\-\s]?ac3|eac3|ddp|dd\+|dolby[\.\-\s]?digital[\.\-\s]?plus)\b", t):
        audio_formats.append("DDP")
    elif re.search(r"\b(ac3|dd5\.1|5\.1)\b", t):
        audio_formats.append("5.1")
    elif re.search(r"\b(aac|2\.0|stereo)\b", t):
        audio_formats.append("2.0")

    # 6. Détection fine de la Langue & Doublage (Spécialité France/Québec/VOST)
    langs = []
    is_multi = bool(re.search(r"\b(multi|multilang)\b", t) or ("french" in t and ("english" in t or "eng" in t)))
    if is_multi:
        langs.append("MULTI")

    # VFF (TrueFrench) vs VFQ (Québécois) vs VF standard
    if re.search(r"\b(vff|truefrench|french[\.\-\s]?true)\b", t):
        langs.append("VFF")
    elif re.search(r"\b(vfq|quebec|french[\.\-\s]?qc)\b", t):
        langs.append("VFQ")
    elif re.search(r"\b(vf|french|fra|fre)\b", t):
        langs.append("VF")
    elif re.search(r"\b(fr)\b", t) and not langs:
        langs.append("FR")

    if re.search(r"\b(vostfr|subfrench|subbed|multisub)\b", t):
        langs.append("VOSTFR")
    elif re.search(r"\b(eng|english)\b", t) and not any(l in langs for l in ("VF", "VFF", "VFQ", "FR")):
        langs.append("VO")

    # Détection badge Debrid Instantané
    is_instant = False
    for b in INSTANT_BADGES:
        if f"[{b.lower()}]" in t or f"[{b}]" in raw or f"({b.lower()})" in t:
            is_instant = True
            break
    if not is_instant and ("⚡" in raw or "cached" in t):
        is_instant = True

    # Construction de la liste des badges propres d'affichage (sans superflu)
    badges = [resolution]
    if source in ("REMUX", "BluRay") and source not in badges:
        badges.append(source)
    if hdr_type != "SDR":
        badges.append(hdr_type)
    if codec == "AV1":
        badges.append("AV1")
    elif codec == "HEVC" and resolution != "4K":
        badges.append("HEVC")

    # Audio principal
    if "Atmos" in audio_formats:
        badges.append("Dolby Atmos")
    elif "TrueHD" in audio_formats:
        badges.append("TrueHD")
    elif "DTS-HD" in audio_formats:
        badges.append("DTS-HD")

    return {
        "resolution": resolution,
        "source": source,
        "hdr_type": hdr_type,
        "codec": codec,
        "audio_formats": audio_formats,
        "langs": langs,
        "is_instant": is_instant,
        "display_badges": badges,
    }


def is_plausible_torrent_size(item: dict, media_type: str = "movie", runtime_minutes: float = None) -> bool:
    """
    Filtre draconien anti-fake et anti-trash :
    - Élimine d'office les enregistrements en salle de cinéma (CAM/Telesync).
    - Vérifie la cohérence physique entre la taille du fichier, la résolution annoncée et la durée.
    - Élimine les réencodages téléphoniques ou faux 4K (ex: 3.8 Go pour un film 4K de 2 heures).
    """
    title = item.get("title") or ""
    # 1. Banishment immédiat des versions volées en salle ou polluées
    if TRASH_TAGS_REGEX.search(title):
        return False

    size_gb = float(item.get("size_gb") or 0.0)
    if not size_gb:
        meta = item.get("meta") or ""
        m_gb = re.search(r"(\d+(?:\.\d+)?)\s*(?:GB|GiB)", meta, re.IGNORECASE)
        m_mb = re.search(r"(\d+(?:\.\d+)?)\s*(?:MB|MiB)", meta, re.IGNORECASE)
        if m_gb:
            size_gb = float(m_gb.group(1))
        elif m_mb:
            size_gb = float(m_mb.group(1)) / 1024.0

    # Si aucune taille n'est connue, on laisse passer sous réserve
    if not size_gb:
        return True

    # Détection de la résolution
    parsed = item.get("parsed_details") or parse_release_details(title, item.get("meta") or "")
    res = parsed.get("resolution", "1080p")
    source = parsed.get("source", "WEB-DL")
    rm = float(runtime_minutes or 0)

    if media_type != "series":
        # FILMS :

        # Fichiers samples, faux fragments ou sous-titres orphelins
        if size_gb < 0.25:
            return False

        if res == "4K":
            # Un vrai flux 4K (même en HEVC/AV1 très compressé) nécessite au minimum ~6.5 Mbps.
            # Pour un film standard de 100 minutes : 100 * 60 * 6.5 Mbps / (8 * 1024) = ~4.76 Go minimum absolu.
            # En dessous de 5.2 Go pour un long-métrage, c'est obligatoirement un fake, un upscale 720p ou une vidéo téléphone.
            min_gb = 5.2
            if rm > 75:
                # Calcul adaptatif basé sur la durée réelle : min 6.8 Mbps
                min_gb = max(5.2, (rm * 60.0 * 6.8) / (8.0 * 1024.0))
            if size_gb < min_gb:
                return False

            # Si c'est annoncé comme un REMUX 4K, le débit doit dépasser ~35 Mbps (min 20 Go pour 1h30)
            if source == "REMUX" and size_gb < 18.0 and (rm == 0 or rm > 70):
                return False

        elif res == "1080p":
            # Pour un 1080p acceptable : min 1.1 Go pour un long métrage (débit min 1.5 Mbps)
            min_gb = 0.9
            if rm > 75:
                min_gb = max(0.9, (rm * 60.0 * 1.5) / (8.0 * 1024.0))
            if size_gb < min_gb:
                return False

            if source == "REMUX" and size_gb < 12.0 and (rm == 0 or rm > 70):
                return False

        else:
            # 720p / SD
            if size_gb < 0.40:
                return False

        # Plafond de sécurité : un seul film ne pèse pas 160 Go (c'est souvent un pack intégral de saga mal taggué)
        if size_gb > 120.0:
            return False

    else:
        # ÉPISODES DE SÉRIE :
        if res == "4K" and size_gb < 1.4:
            return False
        if res == "1080p" and size_gb < 0.28:
            return False
        if size_gb < 0.10:
            return False

    return True


def score_torrent(item: dict, pref_lang: str = "vf", pref_quality: str = "4k", hdr_mode: str = "sdr_pref") -> int:
    """
    Calcule un score de pertinence cinéphile pour classer les flux du meilleur au moins bon :
    - Bonus massif pour le débridage instantané (en cache Real-Debrid/AllDebrid/TorBox).
    - Alignement parfait avec les préférences de langue (VFF > MULTI > VFQ > VOSTFR).
    - Alignement avec la qualité demandée (REMUX > BluRay > WEB-DL).
    - Respect des préférences d'écran (SDR pour écrans classiques, HDR/DV pour écrans compatibles).
    """
    details = item.get("parsed_details") or parse_release_details(item.get("title") or "", item.get("meta") or "")
    score = 0

    # 1. Immédiateté : Le streaming instantané en cache Debrid prime avant tout
    if item.get("is_instant") or details.get("is_instant"):
        score += 3500

    # 2. Score de Qualité Source & Résolution
    res = details["resolution"]
    src = details["source"]
    quals = item.get("qualities") or []

    if pref_quality == "1080p":
        if res == "1080p":
            score += 350
            if src == "REMUX":
                score += 120
            elif src == "BluRay":
                score += 80
        elif res == "4K":
            score += 100
        elif res == "720p":
            score += 50
    else:
        # 4K par défaut
        if res == "4K":
            score += 300
            if src == "REMUX":
                score += 180  # Qualité absolue sans perte
            elif src == "BluRay":
                score += 100
        elif res == "1080p":
            score += 200
            if src == "REMUX":
                score += 140
            elif src == "BluRay":
                score += 80
        elif res == "720p":
            score += 50

    # 3. Langue & Doublage (Point fort KINO)
    langs = details["langs"]
    if pref_lang == "vostfr":
        if "VOSTFR" in langs:
            score += 900
        elif "MULTI" in langs:
            score += 750
        elif "VO" in langs or not langs:
            score += 500
        elif any(l in langs for l in ("VFF", "VF", "FR")):
            score += 200
    else:
        # Préférence Version Française (VF / VFF)
        if "VFF" in langs and "MULTI" in langs:
            score += 950  # Le Graal : VF France officielle + Version originale
        elif "VFF" in langs:
            score += 850
        elif "MULTI" in langs:
            score += 750
        elif "VF" in langs:
            score += 650
        elif "VFQ" in langs:
            score += 450  # Québécois acceptable mais légèrement pénalisé par rapport à la VFF
        elif "FR" in langs:
            score += 400
        elif "VOSTFR" in langs:
            score += 250
        else:
            score += 50

    # 4. Traitement HDR / Dolby Vision / SDR
    hdr = details["hdr_type"]
    if hdr_mode == "hdr_native":
        if "DV" in hdr:
            score += 60
        if "HDR" in hdr:
            score += 50
    elif hdr_mode == "hdr_boost":
        if "SDR" in hdr:
            score += 100
        if "DV" in hdr and "HDR" not in hdr:
            score -= 150
    else:
        # sdr_pref (Par défaut) : évite les rendus trop sombres ou couleurs délavées sur moniteurs SDR
        if "SDR" in hdr:
            score += 140
        if "HDR" in hdr:
            score -= 120
        if "DV" in hdr:
            score -= 220 if "HDR" in hdr else 350

    # 5. Formats Sonores Haute Fidélité
    audios = details["audio_formats"]
    if "Atmos" in audios:
        score += 80
    elif "TrueHD" in audios or "DTS-HD" in audios:
        score += 60
    elif "DDP" in audios or "5.1" in audios:
        score += 35

    # 6. Seeders et Santé du torrent
    seeders = int(item.get("seeders") or 0)
    score += min(seeders * 2, 80)

    # 7. Pénalité d'élimination pour les fakes
    if not is_plausible_torrent_size(item):
        score -= 10000

    title_up = (item.get("title") or "").upper()
    if TRASH_TAGS_REGEX.search(title_up):
        score -= 20000

    # 8. Équilibrage de la taille de fichier
    size_gb = float(item.get("size_gb") or 0.0)
    if pref_quality == "1080p":
        if 1.5 <= size_gb <= 14.0:
            score += 60
        elif size_gb > 25.0:
            score -= 80
    else:
        if 6.0 <= size_gb <= 35.0:
            score += 60
        elif 35.0 < size_gb <= 65.0:
            score += 30  # Bon pour un REMUX 4K

    return score


def parse_stremio_stream(s: dict, default_source: str = "Torrentio") -> dict:
    """Parse un flux Stremio brut en une entité structurée et propre pour KINO."""
    behavior_filename = (s.get("behaviorHints") or {}).get("filename")
    raw_title = s.get("title") or s.get("description") or ""
    source_tag = (s.get("name") or default_source).replace("\n", " ").strip()
    lines = [line.strip() for line in raw_title.split("\n") if line.strip()]
    release_name = lines[0] if lines else "Stream"
    if behavior_filename:
        release_name = behavior_filename
    else:
        release_name = re.sub(r"^[\s\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]+\s*", "", release_name).strip()
    meta_info = clean_meta_text(" • ".join(lines[1:])) if len(lines) > 1 else ""

    # Extraction des seeders
    seeders = int(s.get("seed") or s.get("seeders") or 0)
    if not seeders:
        m_seed = re.search(r"👤\s*(\d+)", raw_title) or re.search(
            r"\b(\d+)\s*(?:seeders?|seeds?|pairs?)\b", raw_title, re.IGNORECASE
        )
        seeders = int(m_seed.group(1)) if m_seed else 0

    # Extraction de la taille en octets / Go
    video_size = (s.get("behaviorHints") or {}).get("videoSize")
    size_bytes = int(s.get("sizebytes") or s.get("size") or video_size or 0)
    size_gb = 0.0
    size_str = ""
    if size_bytes > 0:
        size_gb = round(size_bytes / (1024.0 * 1024.0 * 1024.0), 2)
        size_str = f"{size_gb} GB"
    else:
        m_gb = re.search(r"💾\s*([\d\.]+)\s*GB", raw_title, re.IGNORECASE) or re.search(
            r"([\d\.]+)\s*(?:GB|GiB)", raw_title, re.IGNORECASE
        )
        m_mb = re.search(r"💾\s*([\d\.]+)\s*MB", raw_title, re.IGNORECASE) or re.search(
            r"([\d\.]+)\s*(?:MB|MiB)", raw_title, re.IGNORECASE
        )
        if m_gb:
            size_gb = round(float(m_gb.group(1)), 2)
            size_str = f"{size_gb} GB"
        elif m_mb:
            size_gb = round(float(m_mb.group(1)) / 1024.0, 3)
            size_str = f"{m_mb.group(1)} MB"

    resolve_url = s.get("url", "")
    info_hash = (s.get("infoHash") or "").lower()
    if not info_hash and resolve_url:
        m = re.search(r"/([a-fA-F0-9]{40})/", resolve_url)
        if m:
            info_hash = m.group(1).lower()

    if not info_hash and not resolve_url:
        return None

    # Analyse poussée
    parsed_details = parse_release_details(f"{source_tag} {release_name}", meta_info)
    is_instant = parsed_details["is_instant"] or any(
        f"[{b.lower()}]" in source_tag.lower() for b in INSTANT_BADGES
    )

    magnet = (
        f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(release_name)}"
        if info_hash
        else resolve_url
    )

    display_meta = meta_info
    if display_meta.startswith(f"{default_source} • "):
        display_meta = display_meta[len(f"{default_source} • ") :]
    elif display_meta == default_source:
        display_meta = ""

    return {
        "source": default_source,
        "title": release_name,
        "meta": display_meta,
        "qualities": parsed_details["display_badges"],
        "langs": parsed_details["langs"],
        "magnet": magnet,
        "resolve_url": resolve_url,
        "is_instant": is_instant,
        "info_hash": info_hash,
        "file_idx": s.get("fileIdx"),
        "seeders": seeders,
        "size_gb": size_gb,
        "size_str": size_str,
        "parsed_details": parsed_details,
    }


def search_multi_torrents(
    imdb_id: str,
    media_type: str = "movie",
    season: int = 1,
    episode: int = 1,
    token: str = "",
    tio_key: str = "",
    provider: str = "realdebrid",
    runtime_minutes: float = None,
    sort_by: str = "score",
    pref_lang: str = "vf",
    pref_quality: str = "4k",
    hdr_mode: str = "sdr_pref",
    http_json_fn=None,
    search_apibay_fn=None,
) -> list:
    """
    Exécute une recherche parallèle agressive sur de multiples indexeurs (Torrentio Main, Torrentio FR,
    TPB+, Peerflix, APIBay), déduplique intelligemment et applique les filtres anti-fake.
    """
    if media_type == "series":
        target = f"series/{imdb_id}:{int(season)}:{int(episode)}"
    else:
        target = f"movie/{imdb_id}"

    if token and tio_key:
        tio_main_url = f"https://torrentio.strem.fun/{tio_key}={token}/stream/{target}.json"
        tio_fr_url = f"https://torrentio.strem.fun/providers=torrent9,c411,nyaasi|language=french|{tio_key}={token}/stream/{target}.json"
    else:
        tio_main_url = f"https://torrentio.strem.fun/stream/{target}.json"
        tio_fr_url = f"https://torrentio.strem.fun/providers=torrent9,c411,nyaasi|language=french/stream/{target}.json"

    tpb_url = f"https://thepiratebay-plus.strem.fun/stream/{target}.json"
    peerflix_url = f"https://peerflix.mov/stream/{target}.json"

    # 1. Comet (Successeur ultra-rapide moderne de KnightCrawler)
    comet_url = None
    if token:
        c_service = "realdebrid"
        if provider in ("alldebrid", "torbox", "premiumize", "debridlink"):
            c_service = provider
        comet_cfg = {
            "indexers": ["all"],
            "maxResultsPerResolution": 15,
            "maxSize": 107374182400,
            "cachedOnly": False,
            "removeTrash": True,
            "debridServices": [{"service": c_service, "apiKey": token}],
            "enableTorrent": True,
        }
        b64_cfg = base64.b64encode(json.dumps(comet_cfg).encode("utf-8")).decode("utf-8")
        comet_url = f"https://comet.feels.legal/{b64_cfg}/stream/{target}.json"
    else:
        comet_url = f"https://comet.feels.legal/stream/{target}.json"

    # 2. KnightCrawler (Backup legacy / instances miroir)
    if token and tio_key:
        kc_url = f"https://knightcrawler.elfhosted.com/{tio_key}={token}/stream/{target}.json"
    else:
        kc_url = f"https://knightcrawler.elfhosted.com/stream/{target}.json"

    buckets = {
        "main": [],
        "fr": [],
        "comet": [],
        "knightcrawler": [],
        "tpb": [],
        "peerflix": [],
        "apibay": [],
        "addons": [],
    }

    def _fetch_stremio(key, url, label, timeout_s):
        try:
            if http_json_fn:
                data = http_json_fn(url, timeout=timeout_s)
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "KINO/2.0"})
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

            parsed_list = []
            for s in (data or {}).get("streams", []) or []:
                p = parse_stremio_stream(s, label)
                if p:
                    parsed_list.append(p)
            buckets[key] = parsed_list
        except Exception:
            pass

    def _fetch_apibay():
        try:
            if search_apibay_fn:
                raw_items = search_apibay_fn(imdb_id)
                buckets["apibay"] = raw_items[:25]
        except Exception:
            pass

    def _fetch_addons():
        try:
            import addon_manager
            q = target.split(":")[0] if ":" in target else target
            raw_addons = addon_manager.run_addons_search(
                query=q, media_type=media_type, season=season, episode=episode
            )
            parsed_addons = []
            for it in raw_addons:
                name = it.get("name") or it.get("title") or ""
                p = parse_release_details(name)
                it.update(p)
                parsed_addons.append(it)
            buckets["addons"] = parsed_addons
        except Exception:
            pass

    threads = [
        threading.Thread(target=_fetch_stremio, args=("main", tio_main_url, "Torrentio", 20), daemon=True),
        threading.Thread(target=_fetch_stremio, args=("fr", tio_fr_url, "Torrentio FR", 10), daemon=True),
        threading.Thread(target=_fetch_stremio, args=("comet", comet_url, "Comet", 10), daemon=True),
        threading.Thread(target=_fetch_stremio, args=("knightcrawler", kc_url, "KnightCrawler", 4), daemon=True),
        threading.Thread(target=_fetch_stremio, args=("tpb", tpb_url, "TPB+", 6), daemon=True),
        threading.Thread(target=_fetch_stremio, args=("peerflix", peerflix_url, "Peerflix", 6), daemon=True),
        threading.Thread(target=_fetch_apibay, daemon=True),
        threading.Thread(target=_fetch_addons, daemon=True),
    ]

    for th in threads:
        th.start()
    threads[0].join(timeout=20)
    threads[1].join(timeout=8)
    for th in threads[2:]:
        th.join(timeout=4)

    # Fusion et déduplication par info_hash
    merged = []
    seen_hashes = {}
    for key in ("fr", "main", "addons", "comet", "knightcrawler", "tpb", "peerflix", "apibay"):
        for item in buckets[key]:
            ih = (item.get("info_hash") or "").lower()
            if not ih:
                merged.append(item)
                continue
            if ih in seen_hashes:
                existing = seen_hashes[ih]
                # Si l'un des scrapers a détecté le cache instantané, le transférer
                if item.get("is_instant") and not existing.get("is_instant"):
                    existing["is_instant"] = True
                    existing["resolve_url"] = item.get("resolve_url") or existing.get("resolve_url")
                    for q in item.get("qualities") or []:
                        if q not in existing["qualities"]:
                            existing["qualities"].insert(0, q)
                for lg in item.get("langs") or []:
                    if lg not in existing["langs"]:
                        existing["langs"].append(lg)
                continue

            seen_hashes[ih] = item
            merged.append(item)

    # Filtrage strict des faux torrents et des CAMs
    valid_items = [
        t for t in merged if is_plausible_torrent_size(t, media_type=media_type, runtime_minutes=runtime_minutes)
    ]

    # Attribution du score à chaque item
    for t in valid_items:
        t["score"] = score_torrent(
            t, pref_lang=pref_lang, pref_quality=pref_quality, hdr_mode=hdr_mode
        )

    # Tri selon le critère choisi
    if sort_by == "seeders":
        valid_items.sort(key=lambda x: (x.get("is_instant", False), int(x.get("seeders", 0))), reverse=True)
    elif sort_by == "size":
        valid_items.sort(key=lambda x: (x.get("is_instant", False), float(x.get("size_gb", 0.0))), reverse=True)
    elif sort_by == "quality":
        quality_prio = {"4K": 4, "1080p": 3, "720p": 2, "SD": 1}
        valid_items.sort(
            key=lambda x: (
                x.get("is_instant", False),
                quality_prio.get((x.get("parsed_details") or {}).get("resolution"), 0),
                x.get("score", 0),
            ),
            reverse=True,
        )
    else:
        # "score" par défaut (Recommandé)
        valid_items.sort(key=lambda x: x.get("score", 0), reverse=True)

    return valid_items
