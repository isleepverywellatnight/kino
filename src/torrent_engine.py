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
import collections
import html
import json
import math
import re
import threading
import time
import urllib.parse
import unicodedata
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


_SUBTITLE_LANGUAGE = r"(?:french|fran[cç]ais|fre|fra|fr|english|eng|en|russian|rus|spanish|spa|es|german|ger|deu|italian|ita|japanese|jpn|por|portuguese)"
_SUBTITLE_SEPARATOR = r"[\s._,;:/+\-]+"
_SUBTITLE_WORD = r"(?:subs?|subbed|subtitles?|subtitled|sous[\s._\-]?titres?|sous[\s._\-]?titr[ée]s?)"
FRENCH_AUDIO_LANGS = ("VFF", "VF", "VFQ", "MULTI", "FR")


def _without_subtitle_language_cues(text: str) -> str:
    """Exclude explicit subtitle labels before inferring audio language."""
    text = re.sub(
        rf"\b(?:multi|multilang){_SUBTITLE_SEPARATOR}{_SUBTITLE_WORD}(?!\w)"
        rf"(?:{_SUBTITLE_SEPARATOR}{_SUBTITLE_LANGUAGE}\b)*",
        " ", text,
    )
    text = re.sub(
        rf"\b{_SUBTITLE_WORD}{_SUBTITLE_SEPARATOR}(?:multi|multilang)\b",
        " ", text,
    )
    text = re.sub(
        rf"\b(?:{_SUBTITLE_LANGUAGE}{_SUBTITLE_SEPARATOR}){{1,6}}"
        rf"{_SUBTITLE_WORD}(?!\w)",
        " ", text,
    )
    return re.sub(
        rf"\b{_SUBTITLE_WORD}(?!\w)"
        rf"(?:{_SUBTITLE_SEPARATOR}{_SUBTITLE_LANGUAGE}\b){{1,6}}",
        " ", text,
    )


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
    audio_text = _without_subtitle_language_cues(t)
    langs = []
    # Whole words only: "Revenge" or "Avengers" must not read as an English track.
    is_multi = bool(re.search(r"\b(multi|multilang)\b", audio_text) or
                    (re.search(r"\b(?:true)?french\b", audio_text) and re.search(r"\b(?:eng|english)\b", audio_text)))
    if is_multi:
        langs.append("MULTI")

    # VFF (TrueFrench) vs VFQ (Québécois) vs VF standard
    if re.search(r"\b(vff|truefrench|french[\.\-\s]?true)\b", audio_text):
        langs.append("VFF")
    elif re.search(r"\b(vfq|quebec|french[\.\-\s]?qc)\b", audio_text):
        langs.append("VFQ")
    # VF2 = two French dubs, VFI = international French dub, VOF = French original.
    elif re.search(r"\b(vf|vf2|vfi|vof|french|fra|fre)\b", audio_text):
        langs.append("VF")
    elif re.search(r"\b(fr)\b", audio_text) and not langs:
        langs.append("FR")

    if re.search(r"\b(vostfr|subfrench)\b", t):
        langs.append("VOSTFR")
    elif re.search(r"\b(eng|english)\b", audio_text) and not any(l in langs for l in ("VF", "VFF", "VFQ", "FR")):
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


def clean_title_for_comparison(s: str) -> str:
    """Nettoie le titre d'une release ou d'une œuvre pour une comparaison rigoureuse."""
    if not s:
        return ""
    # Retirer les tags d'équipes au début : [SubsPlease], [Erai-raws], (AnimeKayo), etc.
    s = re.sub(r'^\s*\[[^\]]+\]\s*', '', s)
    s = re.sub(r'^\s*\([^\)]+\)\s*', '', s)
    # Remplacer points et underscores par des espaces
    s = re.sub(r'[\._]', ' ', s)
    # Retirer les années isolées entre parenthèses ou crochets : (2004), [2004]
    s = re.sub(r'[\(\[\{]\s*(?:19\d\d|20\d\d)\s*[\)\]\}]', ' ', s)
    # Tronquer à partir du marqueur de saison/épisode/année : S01E02, S1E2, - 02, 2004, etc.
    s = re.sub(
        r'\s+(?:s\d{1,2}\s*e\d{1,3}|s\d{1,2}|e\d{1,3}|\d{1,2}x\d{1,2}|-\s*s\d{1,2}|-\s*\d{1,4}|\b(?:19\d\d|20\d\d)\b).*$',
        '',
        s,
        flags=re.IGNORECASE
    )
    s = re.sub(r'^[\s\-_:]+|[\s\-_:]+$', '', s)
    return re.sub(r'\s+', ' ', s).strip().lower()


def extract_release_years(text: str) -> list:
    """Extrait toutes les années de production valides (1920-2035) présentes dans une chaîne."""
    return [int(y) for y in re.findall(r'\b(19[2-9]\d|20[0-3]\d)\b', text or '')]


def release_years_outside_title(release_title: str, *titles: str) -> list:
    """Years in a release name, minus numbers that belong to the work's own title:
    « 2001: A Space Odyssey » (1968) or « 2012 » (2009) were rejected as wrong years."""
    title_years = {year for title in titles for year in extract_release_years(title or "")}
    return [year for year in extract_release_years(release_title) if year not in title_years]


IGNORE_RELEASE_WORDS = {
    "naoki", "urasawa", "urasawas", "anime", "series", "complete", "batch", "dual", "audio",
    "sub", "subs", "raw", "raws", "readnfo", "upscale", "original", "color", "uncut",
    "remastered", "edition", "rip", "the", "a", "an", "s", "season", "ep", "episode",
    "vol", "volume", "ova", "ona", "special", "specials", "movie", "film"
}

DIFF_SHOW_WORDS = {
    "re", "monarch", "legacy", "ed", "gein", "dahmer", "jeffrey", "work", "works",
    "high", "musume", "florence", "truck", "trucks", "hunter", "hunters", "university",
    "inc", "strike", "rancher", "chronicles", "garage", "legend", "legends", "island",
    "school", "academy", "quest", "wars", "war"
}


def is_release_relevant(
    filename: str,
    expected_title: str = "",
    expected_year: str = None,
    season: int = 1,
    episode: int = 1,
    media_type: str = "movie",
) -> bool:
    """
    Vérifie avec rigueur si le nom de fichier d'une release torrent correspond réellement
    au média demandé (évite la pollution par des séries homonymes ou des franchises dérivées).
    """
    if not expected_title or not filename:
        return True

    low_file = filename.lower()
    cleaned = re.sub(r"\[.*?\]|\(.*?\)", " ", filename)

    # Extraction du préfixe de titre avant saison/épisode/année/résolution/qualité
    split_regex = (
        r"\b(s\d{1,2}(?:e\d{1,2})?|season\s*\d+|ep\s*\d+|e\d{2}|"
        r"(19\d\d|20\d\d)|2160p|1080p|720p|480p|bluray|web-dl|webrip|hdtv|dvdrip|remux)\b"
    )
    # A number that belongs to the expected title (« 2001: A Space Odyssey », « 2012 ») is not
    # the year marker that ends the title part.
    title_years = set(extract_release_years(expected_title))
    m = next((match for match in re.finditer(split_regex, cleaned, re.IGNORECASE)
              if not (match.group(2) and int(match.group(2)) in title_years)), None)
    title_part = cleaned[: m.start()] if m else cleaned

    norm_title = re.sub(r"[\.\-_]", " ", title_part).strip().lower()

    # A numbered sequel is another film: « Toy.Story.2 » is not « Toy Story ».
    title_tokens = re.findall(r"[a-z0-9]+", norm_title)
    expected_tokens = re.findall(r"[a-z0-9]+", expected_title.lower())
    if expected_tokens and len(title_tokens) > len(expected_tokens) \
            and title_tokens[:len(expected_tokens)] == expected_tokens \
            and re.fullmatch(r"\d{1,2}|ii|iii|iv|vi|vii|viii|ix", title_tokens[len(expected_tokens)]):
        return False

    # Titre principal vs titre avec sous-titre (ex: 'Frieren: Beyond Journey\'s End' -> 'Frieren')
    exp_primary = expected_title.split(":")[0].split(" - ")[0].strip().lower()
    exp_full = expected_title.strip().lower()

    norm_title_clean = re.sub(r"[^a-z0-9]", "", norm_title)
    exp_primary_clean = re.sub(r"[^a-z0-9]", "", exp_primary)

    # 1. Vérification du préfixe 'Re:' (ex: Re:Monster vs Monster, sauf si le titre demandé commence par Re)
    if not exp_primary.startswith("re") and re.search(r"\bre[\:\s\.\-_]?" + re.escape(exp_primary) + r"\b", low_file):
        return False

    # 2. Vérification des mots distinctifs de séries différentes
    words_title = set(re.findall(r"[a-z0-9]+", norm_title))
    words_exp = set(re.findall(r"[a-z0-9]+", exp_primary)).union(set(re.findall(r"[a-z0-9]+", exp_full)))

    extra_words = words_title - words_exp - IGNORE_RELEASE_WORDS
    if extra_words.intersection(DIFF_SHOW_WORDS):
        return False
    if len(words_exp) == 1 and extra_words:
        return False

    # Pour les titres courts (1 ou 2 mots comme 'Monster' ou 'Dark'), filtrer les marqueurs de séries distinctes dans tout le nom
    if len(words_exp) <= 2:
        for d in DIFF_SHOW_WORDS:
            if re.search(r"\b" + re.escape(d) + r"\b", low_file):
                if (
                    d in ("monarch", "gein", "dahmer", "florence")
                    or f"{exp_primary} {d}" in low_file
                    or f"{d} {exp_primary}" in low_file
                    or f"{exp_primary}.{d}" in low_file
                ):
                    return False
        # Vérification singulier vs pluriel pour les titres stricts d'un mot (ex: 'Monster' vs 'Monsters')
        if exp_primary == "monster" and (norm_title_clean in ("monsters", "monsterland") or "monsters." in low_file or "monsters " in low_file or "monsterland" in low_file):
            return False

        # Rejeter les mots composés dérivés d'un titre strict (ex: 'monsterland', 'darkness')
        if len(words_exp) == 1:
            for w in words_title:
                if w.startswith(exp_primary) and len(w) > len(exp_primary):
                    return False

    # 3. Vérification de l'année si elle apparaît explicitement dans le nom de la release
    if expected_year:
        try:
            exp_y = int(str(expected_year)[:4])
            years = release_years_outside_title(filename, expected_title)
            for y in years:
                if y not in (1080, 2160, 720, 480, 576):
                    # Si l'écart dépasse 2 ans, il s'agit d'une autre œuvre
                    if abs(y - exp_y) > 2:
                        return False
        except Exception:
            pass

    return True


def fold_title(text: str) -> str:
    """Accents and ligatures removed: release names rarely carry them (« Amélie » → « Amelie »)."""
    text = (text or "").replace("œ", "oe").replace("Œ", "Oe").replace("æ", "ae").replace("Æ", "Ae")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _title_variants(title: str) -> list:
    """An apostrophe is dropped (« Ch'tis » → « Chtis ») or becomes a separator (« d.Amelie »)."""
    folded = fold_title(title)
    variants = [folded]
    for apostrophe in ("'", "\u2019"):
        if apostrophe in folded:
            variants += [folded.replace(apostrophe, ""), folded.replace(apostrophe, " ")]
    return list(dict.fromkeys(variants))


def is_valid_release_for_show(release_title: str, target_title: str, release_year: str = None) -> bool:
    """Vérifie si le titre de la release correspond bien à l'œuvre ciblée et élimine les faux positifs."""
    if not target_title or not target_title.strip():
        return True
    release_title = fold_title(release_title)
    return any(_matches_work(release_title, variant, release_year) for variant in _title_variants(target_title))


def _matches_work(release_title: str, target_title: str, release_year: str = None) -> bool:

    # 1. Test direct par pertinence sémantique & exclusion de franchises dérivées (Re:Monster, Dahmer, etc.)
    if not is_release_relevant(release_title, expected_title=target_title, expected_year=release_year):
        return False

    # Un titre long doit réellement être présent dans la release : l'année et la
    # taille seules ne suffisent pas à identifier l'œuvre (ex. A Monster Calls).
    target_words = [w for w in re.findall(r"[a-z0-9]+", clean_title_for_comparison(target_title))
                    if w not in {"a", "an", "the", "le", "la", "les", "de", "des", "un", "une"}]
    if len(target_words) >= 2 and not (":" in target_title and len(target_title.split(":", 1)[0].split()) == 1):
        release_words = re.findall(r"[a-z0-9]+", clean_title_for_comparison(release_title))
        distinctive = target_words[: min(3, len(target_words))]
        if not all(word in release_words[:len(distinctive) + 4] for word in distinctive):
            return False

    # 2. Validation stricte de l'année si renseignée (ex: Monster 2004 vs Monster 2026/Ed Gein)
    if release_year:
        try:
            t_yr = int(str(release_year)[:4])
            r_years = release_years_outside_title(release_title, target_title)
            for ry in r_years:
                if abs(ry - t_yr) > 1:
                    return False
        except Exception:
            pass

    return True


# --- Score: ten parts, each answering one question about a release -----------
# score_torrent() adds them up; score_breakdown() shows them one by one.
ScoreProfile = collections.namedtuple(
    "ScoreProfile", "pref_lang pref_quality hdr_mode media_type runtime_minutes target_year title_years priority",
    defaults=("speed",))
# Setting « Priorité »: « speed » (a source ready at once first) or « quality »
# (the chosen quality first, even to be prepared).
SOURCE_PRIORITIES = ("speed", "quality")
QUALITY_FIRST_POINTS = 10000

ANIME_RELEASE_GROUPS = (
    "subsplease", "erai-raws", "asw", "judas", "ember", "vostfree",
    "tsundere", "yameii", "anime rg", "kametsu", "dragsterps", "kayo", "chibiki"
)


def _instant_points(item, details, profile):
    """1. Already cached by the Debrid service: it plays at once, before anything else."""
    return 3500 if item.get("is_instant") or details.get("is_instant") else 0


def _quality_points(item, details, profile):
    """2. Resolution and source, against the preferred quality (REMUX > BluRay > WEB)."""
    res, src = details["resolution"], details["source"]
    if profile.pref_quality == "1080p":
        if res == "1080p":
            return 350 + {"REMUX": 120, "BluRay": 80}.get(src, 0)
        return {"4K": 100, "720p": 50}.get(res, 0)
    # 4K by default
    if res == "4K":
        return 300 + {"REMUX": 180, "BluRay": 100}.get(src, 0)  # REMUX: no loss at all
    if res == "1080p":
        return 200 + {"REMUX": 140, "BluRay": 80}.get(src, 0)
    return 50 if res == "720p" else 0


def _language_points(item, details, profile):
    """3. Language and dubbing, against the preferred language."""
    langs = details["langs"]
    if profile.pref_lang == "vostfr":
        if "VOSTFR" in langs:
            return 900
        if "MULTI" in langs:
            return 750
        if "VO" in langs or not langs:
            return 500
        if any(l in langs for l in ("VFF", "VF", "FR")):
            return 200
        return 0
    # French version preferred
    if "VFF" in langs and "MULTI" in langs:
        return 950  # official French dub and the original version
    # VFQ (Québec) is accepted but just below the VFF.
    for tag, points in (("VFF", 850), ("MULTI", 750), ("VF", 650), ("VFQ", 450), ("FR", 400), ("VOSTFR", 250)):
        if tag in langs:
            return points
    return 50


def _picture_points(item, details, profile):
    """4. HDR / Dolby Vision / SDR, against the screen setting."""
    hdr = details["hdr_type"]
    points = 0
    if profile.hdr_mode == "hdr_native":
        if "DV" in hdr:
            points += 60
        if "HDR" in hdr:
            points += 50
    elif profile.hdr_mode == "hdr_boost":
        if "SDR" in hdr:
            points += 100
        if "DV" in hdr and "HDR" not in hdr:
            points -= 150
    else:
        # sdr_pref (default): the player converts HDR10 (and DV with an HDR base) for a
        # standard screen, measured (docs/playback.md, « Rendu HDR »): an SDR source only
        # wins a tie. Dolby Vision alone can show wrong colours.
        if hdr == "DV":
            points -= 350
        elif hdr == "SDR":
            points += 40
    return points


def _sound_points(item, details, profile):
    """5. High-fidelity sound."""
    audios = details["audio_formats"]
    if "Atmos" in audios:
        return 80
    if "TrueHD" in audios or "DTS-HD" in audios:
        return 60
    if "DDP" in audios or "5.1" in audios:
        return 35
    return 0


def _seeder_points(item, details, profile):
    """6. Seeders: the health of the torrent, capped."""
    return min(int(item.get("seeders") or 0) * 2, 80)


def _fake_penalty(item, details, profile):
    """7. Implausible size for the runtime, or a CAM/TS/trash tag: out of the running."""
    points = 0
    if not is_plausible_torrent_size(item, media_type=profile.media_type, runtime_minutes=profile.runtime_minutes):
        points -= 10000
    if TRASH_TAGS_REGEX.search((item.get("title") or "").upper()):
        points -= 20000
    return points


def _size_points(item, details, profile):
    """8. A file size that suits the preferred quality."""
    size_gb = float(item.get("size_gb") or 0.0)
    if profile.pref_quality == "1080p":
        if 1.5 <= size_gb <= 14.0:
            return 60
        return -80 if size_gb > 25.0 else 0
    if 6.0 <= size_gb <= 35.0:
        return 60
    return 30 if 35.0 < size_gb <= 65.0 else 0  # 35-65 Go: a good 4K REMUX


def _anime_group_points(item, details, profile):
    """9. Known anime release and fansub groups, a little more from Nyaa."""
    raw_title_lower = (item.get("title") or "").lower()
    if not any(grp in raw_title_lower for grp in ANIME_RELEASE_GROUPS):
        return 0
    from_nyaa = "nyaa" in (item.get("source") or "").lower() or "nyaasi" in raw_title_lower
    return 260 + (80 if from_nyaa else 0)


def _year_points(item, details, profile):
    """10. The production year: confirmed in the title, or clearly another work (remake, namesake)."""
    if not profile.target_year:
        return 0
    try:
        t_yr = int(str(profile.target_year)[:4])
        excluded = set(profile.title_years)
        r_years = [y for y in extract_release_years(item.get("title") or "") if y not in excluded]
        if t_yr in r_years:
            return 500
        if r_years and any(abs(ry - t_yr) > 1 for ry in r_years):
            return -10000  # e.g. 2026 against 2004: a false match
    except Exception:
        pass
    return 0


def _priority_points(item, details, profile):
    """11. « La qualité choisie d'abord »: in the chosen quality and an accepted language,
    a source comes before all the others, ready or not. Dolby Vision alone (colours
    may be wrong), fakes and other years stay out of it. Nothing in « Lancer vite »."""
    if profile.priority != "quality":
        return 0
    wanted = "1080p" if profile.pref_quality == "1080p" else "4K"
    raw = f"{item.get('title') or ''} {item.get('meta') or ''}"
    if details.get("resolution") != wanted or not _ANNOUNCED_RESOLUTION.search(raw):
        return 0
    hdr = details.get("hdr_type") or ""
    if "DV" in hdr and "HDR" not in hdr:
        return 0
    if _fake_penalty(item, details, profile) < 0 or _year_points(item, details, profile) < 0:
        return 0
    if _language_points(item, details, profile) < (500 if profile.pref_lang == "vostfr" else 400):
        return 0
    return QUALITY_FIRST_POINTS


SCORE_PARTS = (
    _instant_points, _quality_points, _language_points, _picture_points, _sound_points,
    _seeder_points, _fake_penalty, _size_points, _anime_group_points, _year_points, _priority_points,
)


def score_breakdown(
    item: dict,
    pref_lang: str = "vf",
    pref_quality: str = "4k",
    hdr_mode: str = "sdr_pref",
    media_type: str = "movie",
    runtime_minutes: float = None,
    target_year: str = "",
    title_years=(),
    priority: str = "speed",
) -> list:
    """[(part name, points)] in the order of SCORE_PARTS."""
    details = release_details(item)
    profile = ScoreProfile(pref_lang, pref_quality, hdr_mode, media_type, runtime_minutes, target_year, title_years, priority)
    return score_parts(item, details, profile)


def release_details(item):
    return item.get("parsed_details") or parse_release_details(item.get("title") or "", item.get("meta") or "")


def score_parts(item, details, profile):
    return [(part.__name__.strip("_"), part(item, details, profile)) for part in SCORE_PARTS]


def score_torrent(
    item: dict,
    pref_lang: str = "vf",
    pref_quality: str = "4k",
    hdr_mode: str = "sdr_pref",
    media_type: str = "movie",
    runtime_minutes: float = None,
    target_year: str = "",
    title_years=(),
    priority: str = "speed",
) -> int:
    """
    Calcule un score de pertinence cinéphile pour classer les flux du meilleur au moins bon :
    - Bonus massif pour le débridage instantané (en cache Real-Debrid/AllDebrid/TorBox).
    - Alignement parfait avec les préférences de langue (VFF > MULTI > VFQ > VOSTFR).
    - Alignement avec la qualité demandée (REMUX > BluRay > WEB-DL).
    - Respect des préférences d'écran (SDR pour écrans classiques, HDR/DV pour écrans compatibles).
    - Vérification rigoureuse de la taille selon le type (film vs épisode de série).
    - Bonus pour l'année exacte de production et élimination des homonymes d'autres années.
    Les dix parties sont les fonctions de SCORE_PARTS.
    """
    return sum(points for _, points in score_breakdown(
        item, pref_lang=pref_lang, pref_quality=pref_quality, hdr_mode=hdr_mode, media_type=media_type,
        runtime_minutes=runtime_minutes, target_year=target_year, title_years=title_years, priority=priority))


# --- Pourquoi cette source : each score part in plain words -------------------
# A reason is {"part", "text", "tone", "points"}; tone is « good », « neutral »
# or « bad ». Parts that say nothing useful (no sound tag, few seeders) give none.
_ANNOUNCED_RESOLUTION = re.compile(r"\b(?:2160p|4k|uhd|1080p|fhd|1080i|720p|480p|576p|sd|dvdrip)\b", re.IGNORECASE)


def _quality_reason(points, item, details, profile):
    raw = f"{item.get('title') or ''} {item.get('meta') or ''}"
    if not _ANNOUNCED_RESOLUTION.search(raw):
        return "Qualité non indiquée", "neutral"
    resolution = details.get("resolution") or ""
    label = resolution + {"REMUX": " REMUX", "BluRay": " Blu-ray"}.get(details.get("source"), "")
    wanted = "4K" if profile.pref_quality != "1080p" else "1080p"
    if resolution == wanted:
        return f"{label}, la qualité choisie", "good"
    if resolution == "4K":
        return f"{label}, au-dessus de la qualité choisie", "neutral"
    if resolution == "1080p":
        return label, "good" if points >= 200 else "neutral"
    return f"{label}, en dessous de la qualité choisie", "bad"


def _language_reason(points, item, details, profile):
    langs = details.get("langs") or []
    if profile.pref_lang == "vostfr":
        if "VOSTFR" in langs:
            return "VOSTFR, la langue choisie", "good"
        if "MULTI" in langs:
            return "Plusieurs langues", "good"
        if "VO" in langs or not langs:
            return "Version originale, sous-titres non indiqués", "neutral"
        return "Doublée en français", "bad"
    if "VFF" in langs and "MULTI" in langs:
        return "VF + VO", "good"
    for tag, text, tone in (("VFF", "VF (France)", "good"), ("MULTI", "Plusieurs langues", "good"), ("VF", "VF", "good"),
                            ("VFQ", "VF québécoise", "neutral"), ("FR", "Français", "neutral"), ("VOSTFR", "VOSTFR seulement", "bad")):
        if tag in langs:
            return text, tone
    return "Pas de VF annoncée", "bad"


def _picture_reason(points, item, details, profile):
    hdr = details.get("hdr_type") or ""
    if profile.hdr_mode == "hdr_native":
        return ("HDR / Dolby Vision, pour votre écran", "good") if points > 0 else None
    if profile.hdr_mode == "hdr_boost":
        if points > 0:
            return "Image SDR, idéale pour l’amélioration HDR", "good"
        return ("Dolby Vision seul : couleurs faussées possibles", "bad") if points < 0 else None
    if "DV" in hdr and "HDR" not in hdr:
        return "Dolby Vision seul : couleurs faussées possibles", "bad"
    if "HDR" in hdr or "DV" in hdr:
        # Measured in the native player (docs/playback.md, « Rendu HDR »): converted well.
        return "HDR, converti pour votre écran standard", "neutral"
    return ("Image SDR, adaptée à votre écran", "good") if points > 0 else None


def _reason(name, points, item, details, profile):
    """(text, tone) for one score part, or None."""
    if name == "instant_points":
        return ("Prête tout de suite", "good") if points else ("Disponibilité immédiate non signalée", "bad")
    if name == "quality_points":
        return _quality_reason(points, item, details, profile)
    if name == "language_points":
        return _language_reason(points, item, details, profile)
    if name == "picture_points":
        return _picture_reason(points, item, details, profile)
    if name == "sound_points":
        return {80: ("Son Dolby Atmos", "good"), 60: ("Son haute fidélité (TrueHD, DTS-HD)", "good"),
                35: ("Son 5.1", "good")}.get(points)
    if name == "seeder_points":
        seeders = int(item.get("seeders") or 0)
        return (f"Bien partagée ({seeders} pairs)", "good") if points >= 40 else None
    if name == "fake_penalty":
        if points <= -20000:
            return "Copie filmée en salle (CAM, TS)", "bad"
        return ("Taille improbable pour la durée", "bad") if points < 0 else None
    if name == "size_points":
        if points == 60:
            return "Taille adaptée à la qualité", "good"
        if points == 30:
            return "Fichier lourd (35 à 65 Go)", "neutral"
        return ("Fichier très lourd", "bad") if points < 0 else None
    if name == "anime_group_points":
        if not points:
            return None
        return ("Équipe anime reconnue, via Nyaa", "good") if points > 260 else ("Équipe anime reconnue", "good")
    if name == "priority_points":
        return ("La qualité choisie d’abord", "good") if points > 0 else None
    if name == "year_points":
        if points > 0:
            return f"Année {str(profile.target_year)[:4]} confirmée", "good"
        return ("Autre année : sans doute une autre œuvre", "bad") if points < 0 else None
    return None


def score_reasons(item, details, parts, profile):
    """The reasons behind a score, in the order of SCORE_PARTS."""
    reasons = []
    for name, points in parts:
        found = _reason(name, points, item, details, profile)
        if found:
            reasons.append({"part": name, "text": found[0], "tone": found[1], "points": points})
    return reasons


def release_episode_status(title: str, season: int, episode: int, absolute_episode: int = None) -> str:
    """Classify explicit episode markers; an unmarked season pack stays uncertain."""
    title = title or ""
    match = re.search(
        r"\bs(\d{1,2})[. _-]*e(\d{1,3})(?:[. _]*(-)[. _]*e?(\d{1,3})|[. _]*e(\d{1,3}))?",
        title, re.IGNORECASE,
    )
    if match:
        found_season, first = int(match.group(1)), int(match.group(2))
        if found_season != int(season):
            return "mismatch"
        if match.group(3) and match.group(4):
            return "match" if first <= int(episode) <= int(match.group(4)) else "mismatch"
        episodes = {first}
        if match.group(5):
            episodes.add(int(match.group(5)))
        return "match" if int(episode) in episodes else "mismatch"
    alternate = re.search(r"\b(\d{1,2})x(\d{1,3})\b", title, re.IGNORECASE)
    if alternate:
        return "match" if (int(alternate.group(1)), int(alternate.group(2))) == (int(season), int(episode)) else "mismatch"
    # Anime releases number episodes absolutely: « [SubsPlease] Frieren - 12 (1080p) ».
    # Only season 1 maps safely (absolute 12 is S01E12); later seasons stay uncertain.
    absolute = re.search(r"\s-\s(\d{1,3})(?:v\d)?(?=[\s\[\(.]|$)(?!\s*[-~]\s*\d)", title)
    if absolute:
        before = title[:absolute.start()]
        # « 2nd Season - 03 », « Season 2 - 03 », « S2 - 03 »: the number counts within that season.
        named = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\s+season\b|\bseason\s*(\d{1,2})\b|\bs(\d{1,2})\b",
                          before, re.IGNORECASE)
        if named:
            named_season = int(next(group for group in named.groups() if group))
            if named_season != int(season):
                return "mismatch"
            return "match" if int(absolute.group(1)) == int(episode) else "mismatch"
        # « Part 2 - 01 » or « Overlord II - 05 » may be renumbered split cours: stay uncertain.
        renumbered = re.search(r"\b(?:part|cour)\s*\d|\b(?:ii|iii|iv)\b", before, re.IGNORECASE)
        number = int(absolute.group(1))
        if not renumbered and int(season) == 1:
            return "match" if number == int(episode) else "mismatch"
        # Later seasons: compare with the absolute number computed from the episode list.
        if not renumbered and absolute_episode:
            if number == int(absolute_episode):
                return "match"
            # Same as the in-season number: the group may restart its numbering each season.
            return "unknown" if number == int(episode) else "mismatch"
    # No hyphen between the word and the number: « Season - 03 » is an episode, not season 3.
    season_pack = re.search(r"\b(?:season|s)[. _]*0?(\d{1,2})\b", title, re.IGNORECASE)
    if season_pack and int(season_pack.group(1)) != int(season):
        return "mismatch"
    return "unknown"


def explain_torrent_recommendation(item: dict, pref_lang: str, pref_quality: str,
                                   title_checked: bool, episode_status: str = "") -> dict:
    """Explain signals from the same metadata used by the ranking, without inventing facts."""
    details = item.get("parsed_details") or parse_release_details(item.get("title") or "", item.get("meta") or "")
    reasons, warnings = [], []
    if title_checked:
        reasons.append("Titre cohérent")
    else:
        warnings.append("Titre non vérifié")
    if episode_status == "match":
        reasons.append("Épisode indiqué")
    elif episode_status == "unknown":
        warnings.append("Épisode à vérifier dans le fichier")
    if item.get("is_instant") or details.get("is_instant"):
        reasons.append("Disponibilité immédiate signalée")
    langs = details.get("langs") or []
    preferred = ["VOSTFR"] if pref_lang == "vostfr" else ["VFF", "VF", "MULTI", "FR"]
    chosen_lang = next((language for language in preferred if language in langs), None)
    if chosen_lang:
        reasons.append(f"Langue souhaitée : {chosen_lang}")
    elif not langs:
        warnings.append("Langue non indiquée")
    elif pref_lang == "vostfr":
        if "VO" in langs:
            warnings.append("Sous-titres français non indiqués")
    elif not any(language in langs for language in FRENCH_AUDIO_LANGS):
        # VO and VOSTFR both announce original audio only.
        warnings.append("Audio français non indiqué")
    raw = f"{item.get('title') or ''} {item.get('meta') or ''}"
    resolution = details.get("resolution")
    if resolution and re.search(r"\b(?:2160p|4k|uhd|1080p|fhd|1080i|720p|480p|576p)\b", raw, re.IGNORECASE):
        label = "Qualité souhaitée" if resolution.lower() == str(pref_quality).lower() else "Qualité annoncée"
        reasons.append(f"{label} : {resolution}")
    if details.get("audio_formats") and any(codec in details["audio_formats"] for codec in ("DTS", "DTS-HD", "TrueHD")):
        warnings.append("Compatibilité audio à vérifier")
    return {"reasons": reasons, "warnings": warnings}


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
    # Some indexers send HTML entities (« Les Mis&eacute;rables »).
    release_name = html.unescape(release_name)
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
    # The indexer's name (e.g. "Torrentio FR") does not describe the file's audio.
    parsed_details = parse_release_details(release_name, meta_info)
    is_instant = parsed_details["is_instant"] or parse_release_details(source_tag)["is_instant"] or any(
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


# --- Source search, step by step ---------------------------------------------
# search_multi_torrents(): the indexers' addresses, their answers fetched side
# by side, one entry per torrent, the releases that fit, then the ranking.

# Merge order: the French answers first, so their details win on a duplicate.
MERGE_ORDER = ("fr", "anime", "main", "addons", "comet", "knightcrawler", "tpb", "peerflix", "apibay")


def stream_target(imdb_id, media_type="movie", season=1, episode=1):
    """The Stremio path of a film or an episode."""
    if media_type in ("series", "anime", "tv"):
        return f"series/{imdb_id}:{int(season)}:{int(episode)}"
    return f"movie/{imdb_id}"


def indexer_sources(target, token="", tio_key="", provider="realdebrid"):
    """[(bucket, url, label, timeout)] of the Stremio indexers, in launch order."""
    if token and tio_key:
        tio_main_url = f"https://torrentio.strem.fun/{tio_key}={token}/stream/{target}.json"
        tio_fr_url = f"https://torrentio.strem.fun/providers=torrent9,c411,nyaasi|language=french|{tio_key}={token}/stream/{target}.json"
        tio_anime_url = f"https://torrentio.strem.fun/providers=nyaasi,tokyotosho,anidex,torrent9|language=japanese,french|{tio_key}={token}/stream/{target}.json"
        kc_url = f"https://knightcrawler.elfhosted.com/{tio_key}={token}/stream/{target}.json"
    else:
        tio_main_url = f"https://torrentio.strem.fun/stream/{target}.json"
        tio_fr_url = f"https://torrentio.strem.fun/providers=torrent9,c411,nyaasi|language=french/stream/{target}.json"
        tio_anime_url = f"https://torrentio.strem.fun/providers=nyaasi,tokyotosho,anidex,torrent9|language=japanese,french/stream/{target}.json"
        # KnightCrawler: legacy backup and mirror instances
        kc_url = f"https://knightcrawler.elfhosted.com/stream/{target}.json"
    return [
        ("main", tio_main_url, "Torrentio", 20),
        ("fr", tio_fr_url, "Torrentio FR", 10),
        ("anime", tio_anime_url, "Torrentio Anime (Nyaa)", 10),
        ("comet", comet_url(target, token, provider), "Comet", 10),
        ("knightcrawler", kc_url, "KnightCrawler", 4),
        ("tpb", f"https://thepiratebay-plus.strem.fun/stream/{target}.json", "TPB+", 6),
        ("peerflix", f"https://peerflix.mov/stream/{target}.json", "Peerflix", 6),
    ]


def comet_url(target, token="", provider="realdebrid"):
    """Comet (the fast successor of KnightCrawler), with the Debrid service when a key is known."""
    if not token:
        return f"https://comet.feels.legal/stream/{target}.json"
    c_service = provider if provider in ("alldebrid", "torbox", "premiumize", "debridlink") else "realdebrid"
    comet_cfg = {
        "indexers": ["all"],
        # 0 = no cap. A cap of 15 per resolution kept 2 French releases out of 31
        # for A Monster Calls; KINO ranks and filters the full list itself.
        "maxResultsPerResolution": 0,
        "maxSize": 107374182400,
        "cachedOnly": False,
        "removeTrash": True,
        "debridServices": [{"service": c_service, "apiKey": token}],
        "enableTorrent": True,
    }
    b64_cfg = base64.b64encode(json.dumps(comet_cfg).encode("utf-8")).decode("utf-8")
    return f"https://comet.feels.legal/{b64_cfg}/stream/{target}.json"


def fetch_streams(sources, target, imdb_id, media_type, season, episode, *, query_title="", http_json_fn=None,
                  search_apibay_fn=None):
    """Every indexer at once, plus APIBay and the add-ons: {bucket: [parsed releases]}.
    Torrentio waits up to 20 s, its French and anime lists 8 s, the others 4 s:
    a late answer is left out. A failing indexer leaves its bucket empty."""
    buckets = {key: [] for key in ("main", "fr", "anime", "comet", "knightcrawler", "tpb", "peerflix", "apibay", "addons")}

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
                raw_items = search_apibay_fn(query_title or imdb_id)
                buckets["apibay"] = raw_items[:25]
        except Exception:
            pass

    def _fetch_addons():
        try:
            import addon_manager
            q = query_title.strip() if query_title else (target.split(":")[0] if ":" in target else target)
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

    threads = [threading.Thread(target=_fetch_stremio, args=source, daemon=True) for source in sources]
    threads += [threading.Thread(target=_fetch_apibay, daemon=True), threading.Thread(target=_fetch_addons, daemon=True)]
    for th in threads:
        th.start()
    threads[0].join(timeout=20)
    threads[1].join(timeout=8)
    threads[2].join(timeout=8)
    for th in threads[3:]:
        th.join(timeout=4)
    return buckets


def merge_streams(buckets):
    """One entry per info_hash. A duplicate adds its languages, and its instant
    availability (with its link and qualities) when the first copy had none."""
    merged = []
    seen_hashes = {}
    for key in MERGE_ORDER:
        for item in buckets[key]:
            ih = (item.get("info_hash") or "").lower()
            if not ih:
                merged.append(item)
                continue
            if ih in seen_hashes:
                existing = seen_hashes[ih]
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
    return merged


def keep_relevant_streams(items, *, media_type, season, episode, absolute_episode, runtime_minutes, titles, release_year,
                          pref_lang, pref_quality):
    """Drops fakes, CAMs, other shows and other episodes; explains each release kept."""
    kept = []
    for t in items:
        if not is_plausible_torrent_size(t, media_type=media_type, runtime_minutes=runtime_minutes):
            continue
        rtitle = t.get("title") or ""
        if titles and not any(is_valid_release_for_show(rtitle, name, release_year=release_year) for name in titles if name):
            continue
        episode_status = release_episode_status(rtitle, season, episode, absolute_episode) if media_type in ("series", "anime", "tv") else ""
        if episode_status == "mismatch":
            continue
        t["recommendation"] = explain_torrent_recommendation(
            t, pref_lang, pref_quality, bool(titles), episode_status,
        )
        kept.append(t)
    return kept


def rank_streams(items, sort_by="score", *, titles, pref_lang, pref_quality, hdr_mode, media_type, runtime_minutes, release_year,
                 source_priority="speed"):
    """Scores each release (with its parts and reasons: « Pourquoi cette source »),
    then sorts: « score » (recommended), seeders, size or quality."""
    title_years = {year for name in titles for year in extract_release_years(name)}
    priority = source_priority if source_priority in SOURCE_PRIORITIES else "speed"
    profile = ScoreProfile(pref_lang, pref_quality, hdr_mode, media_type, runtime_minutes, release_year, title_years, priority)
    for t in items:
        details = release_details(t)
        parts = score_parts(t, details, profile)
        # The same sum as score_torrent(); the parts and their reasons explain it.
        t["score"] = sum(points for _, points in parts)
        t["score_parts"] = dict(parts)
        t["why"] = score_reasons(t, details, parts, profile)

    if sort_by == "seeders":
        items.sort(key=lambda x: (x.get("is_instant", False), int(x.get("seeders", 0))), reverse=True)
    elif sort_by == "size":
        items.sort(key=lambda x: (x.get("is_instant", False), float(x.get("size_gb", 0.0))), reverse=True)
    elif sort_by == "quality":
        quality_prio = {"4K": 4, "1080p": 3, "720p": 2, "SD": 1}
        items.sort(
            key=lambda x: (
                x.get("is_instant", False),
                quality_prio.get((x.get("parsed_details") or {}).get("resolution"), 0),
                x.get("score", 0),
            ),
            reverse=True,
        )
    else:
        items.sort(key=lambda x: x.get("score", 0), reverse=True)
    return items


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
    query_title: str = "",
    release_year: str = "",
    title_aliases=(),
    absolute_episode: int = None,
    source_priority: str = "speed",
) -> list:
    """
    Exécute une recherche parallèle agressive sur de multiples indexeurs (Torrentio Main, Torrentio FR,
    Torrentio Anime, TPB+, Peerflix, APIBay), déduplique intelligemment et applique les filtres anti-fake.
    """
    target = stream_target(imdb_id, media_type, season, episode)
    buckets = fetch_streams(indexer_sources(target, token, tio_key, provider), target, imdb_id, media_type, season, episode,
                            query_title=query_title, http_json_fn=http_json_fn, search_apibay_fn=search_apibay_fn)
    titles = tuple(name for name in (query_title, *title_aliases) if name)
    relevant = keep_relevant_streams(
        merge_streams(buckets), media_type=media_type, season=season, episode=episode, absolute_episode=absolute_episode,
        runtime_minutes=runtime_minutes, titles=titles, release_year=release_year, pref_lang=pref_lang, pref_quality=pref_quality)
    return rank_streams(relevant, sort_by, titles=titles, pref_lang=pref_lang, pref_quality=pref_quality, hdr_mode=hdr_mode,
                        media_type=media_type, runtime_minutes=runtime_minutes, release_year=release_year,
                        source_priority=source_priority)
