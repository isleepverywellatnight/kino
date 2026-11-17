#!/usr/bin/env python3
"""
KINO Stream Engine — Deep Stream Discovery & Debrid Resolution Module
Consolide la recherche de sources torrents multi-scrapers, le filtrage sémantique
des homonymes, le calcul de score cinéphile et la résolution de flux débridés
derrière une frontière unique et étanche.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import hashlib
import json
import re
import urllib.parse

import config
import kino_db
import meta_engine
from meta_engine import IMDB_ID_ALIASES

# Re-export des détails de release pour rétrocompatibilité
import torrent_engine
from torrent_engine import (
    parse_release_details,
    clean_title_for_comparison,
    extract_release_years,
    is_valid_release_for_show,
    is_release_relevant,
    is_plausible_torrent_size,
    score_torrent,
)


# ==============================================================================
# EXCEPTIONS DU DOMAINE
# ==============================================================================

class StreamEngineError(Exception):
    """Exception de base pour les erreurs du moteur de flux."""
    pass


class DebridResolutionError(StreamEngineError):
    """Levée lorsqu'un flux ne peut pas être débridé ou n'est plus en cache."""
    pass


class StreamNotFoundError(StreamEngineError):
    """Levée lorsqu'aucun flux valide ne correspond à la requête."""
    pass


# ==============================================================================
# CONTRATS DE DONNÉES (THE SEAM)
# ==============================================================================

@dataclass
class StreamQuery:
    """Spécification canonique d'une recherche de média."""
    imdb_id: str
    media_type: str = "movie"
    season: int = 1
    episode: int = 1
    title: str = ""
    release_year: str = ""
    runtime_minutes: Optional[int] = None
    sort_by: str = "score"
    pref_lang: str = "vf"
    pref_quality: str = "4k"
    hdr_mode: str = "sdr_pref"
    source_priority: str = "speed"

    @classmethod
    def from_params(cls, params: dict, cfg: dict = None) -> "StreamQuery":
        """Instancie et nettoie une StreamQuery depuis les paramètres HTTP ou d'appel."""
        if cfg is None:
            cfg = config.load_config()

        raw_id = (params.get("imdb_id") or params.get("id") or "").strip()
        canonical_id = IMDB_ID_ALIASES.get(raw_id, raw_id)

        mtype = (params.get("type") or params.get("media_type") or "movie").strip().lower()
        if mtype == "tv":
            mtype = "series"
        if mtype not in ("movie", "series", "anime"):
            mtype = "movie"

        try:
            s = int(params.get("season", 1) or 1)
        except Exception:
            s = 1

        try:
            ep = int(params.get("episode", 1) or 1)
        except Exception:
            ep = 1

        raw_title = (params.get("title") or params.get("q") or "").strip()
        clean_title = raw_title.split(" — ")[0].strip() if " — " in raw_title else raw_title

        raw_year = str(params.get("year") or "").strip()
        m_yr = re.search(r"\b(19\d\d|20\d\d)\b", raw_year)
        clean_year = m_yr.group(1) if m_yr else raw_year

        rt_min = None
        raw_rt = params.get("runtime")
        if raw_rt:
            m_rt = re.search(r"(\d+)", str(raw_rt))
            if m_rt:
                rt_min = int(m_rt.group(1))

        return cls(
            imdb_id=canonical_id,
            media_type=mtype,
            season=s,
            episode=ep,
            title=clean_title,
            release_year=clean_year,
            runtime_minutes=rt_min,
            sort_by=params.get("sort_by", "score"),
            pref_lang=cfg.get("pref_lang", "vf"),
            pref_quality=cfg.get("pref_quality", "4k"),
            hdr_mode=cfg.get("hdr_mode", "sdr_pref"),
            source_priority="quality" if cfg.get("source_priority") == "quality" else "speed",
        )

    def cache_key(self, provider_id: str, has_token: bool) -> str:
        """Génère une clé de cache déterministe pour cette requête."""
        raw = f"stream_v14:{provider_id}:{self.media_type}:{self.imdb_id}:{self.season}:{self.episode}:{has_token}:{self.sort_by}:{self.title.lower()}:{self.release_year}:{self.runtime_minutes}:{self.pref_lang}:{self.pref_quality}:{self.hdr_mode}:{self.source_priority}"
        return raw


def stream_candidate_key(candidate):
    """Stable opaque identifier used to avoid retrying a failed source."""
    identity = candidate.get("info_hash") or candidate.get("magnet") or candidate.get("resolve_url")
    if not identity:
        identity = f"{candidate.get('source', '')}:{candidate.get('title', '')}"
    return hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:32]


@dataclass
class SourceAttempts:
    """What try_sources() did: the result of `use` when a source worked."""
    found: bool = False
    result: Any = None
    key: str = ""
    tried: List[str] = field(default_factory=list)
    last_error: Optional[BaseException] = None


def try_sources(token, candidates, query, *, debrid, use, season=None, episode=None, aliases_for=None, skip=(), stop=None,
                limit=10) -> SourceAttempts:
    """Tries the first `limit` sources in their order: the Debrid link, the
    right file in it, then `use(candidate, debrid_answer, file, key)`.

    A source set aside (`skip`) still counts in the `limit`. A source that is
    not ready, holds no right file, or fails (`use` included) gives way to the
    next; an error for which `stop(error)` is true ends everything at once."""
    attempts = SourceAttempts()
    for candidate in candidates[:limit]:
        key = stream_candidate_key(candidate)
        if key in skip:
            continue
        attempts.tried.append(key)
        try:
            answer = debrid(token, candidate.get("magnet", ""), season=season, episode=episode,
                            resolve_url=candidate.get("resolve_url", ""))
            if not (answer.get("ready") and answer.get("files")):
                continue
            if aliases_for is None:
                target = select_playable_file(query, answer["files"])
            else:
                target = select_playable_file(query, answer["files"], aliases_for(candidate))
            if not target:
                continue
            attempts.result = use(candidate, answer, target, key)
            attempts.found, attempts.key = True, key
            return attempts
        except Exception as error:
            if stop is not None and stop(error):
                raise
            attempts.last_error = error
    return attempts


def select_playable_file(query: StreamQuery, files: List[dict], title_aliases=()) -> Optional[dict]:
    """Choose a debrided video whose filename does not contradict the Stream Query."""
    available = [item for item in (files or []) if isinstance(item, dict) and
                 isinstance(item.get("download"), str) and item["download"].startswith(("http://", "https://"))]
    if not available:
        return None

    def size(item):
        try:
            return int(item.get("filesize_bytes") or 0)
        except (TypeError, ValueError):
            return 0

    if query.media_type in ("series", "anime"):
        # Only later seasons need the absolute number; never let it break the selection.
        absolute = (meta_engine.absolute_episode_number(getattr(query, "imdb_id", ""), query.season, query.episode)
                    if int(query.season or 1) > 1 and getattr(query, "imdb_id", "") else None)
        statuses = [(item, torrent_engine.release_episode_status(
            item.get("filename") or "", query.season, query.episode, absolute)) for item in available]
        eligible = [(item, status) for item, status in statuses if status != "mismatch"]
        explicit = [item for item, status in eligible if status == "match"]
        if explicit:
            return max(explicit, key=size)
        flagged = [item for item, _status in eligible if item.get("is_target_ep")]
        if flagged:
            return max(flagged, key=size)
        return eligible[0][0] if len(available) == 1 and eligible else None

    aliases = list(dict.fromkeys(str(name).strip() for name in (query.title, *title_aliases) if name))
    matching = [item for item in available if any(
        torrent_engine.is_valid_release_for_show(item.get("filename") or "", name, query.release_year)
        for name in aliases
    )]
    if matching:
        return max(matching, key=size)
    if len(available) != 1:
        return None
    filename = available[0].get("filename") or ""
    basename = re.split(r"[/\\]", filename)[-1]
    stem = re.sub(r"\.(?:mkv|mp4|avi|mov|m4v|wmv|ts|webm)$", "", basename, flags=re.IGNORECASE)
    normalized = re.sub(r"[^a-z0-9]+", " ", torrent_engine.clean_title_for_comparison(stem)).strip()
    generic = (not normalized or bool(re.fullmatch(r"[0-9a-f]{8,}", normalized)) or
               bool(re.fullmatch(r"(?:video|movie|film|stream|download|file|output|feature|main)(?:\s*(?:\d+|hd|1080p|2160p|720p|4k))*", normalized)))
    # A unique generic filename is inconclusive; trust the already validated Stream.
    # An explicit unmatched title is a risk even without a release year.
    if not aliases or generic:
        return available[0]
    return None


@dataclass
class PlayableStream:
    """Représente un flux directement lisible par le lecteur externe."""
    url: str
    title: str
    quality: str = "HD"
    provider: str = "realdebrid"
    is_instant: bool = True
    info_hash: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "quality": self.quality,
            "provider": self.provider,
            "is_instant": self.is_instant,
            "info_hash": self.info_hash,
            "details": self.details,
        }


# ==============================================================================
# ADAPTATEURS DE DÉBRIDAGE (PORTS & ADAPTERS)
# ==============================================================================

class DebridAdapter:
    """Contrat d'interface pour un fournisseur de débridage."""
    name: str = "none"

    def get_user_info(self) -> dict:
        raise NotImplementedError

    def unrestrict_link(self, link: str) -> str:
        raise NotImplementedError

    def resolve_magnet(self, magnet: str, file_idx: Optional[int] = None) -> str:
        raise NotImplementedError

    def check_instant_availability(self, info_hashes: List[str]) -> Dict[str, bool]:
        return {}


class RealDebridAdapter(DebridAdapter):
    name = "realdebrid"

    def __init__(self, token: str):
        self.token = (token or "").strip()
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def get_user_info(self) -> dict:
        import debrid_engine
        return debrid_engine.rd_get_user(self.token, provider="realdebrid")

    def unrestrict_link(self, link: str) -> str:
        import debrid_engine
        return debrid_engine.rd_unrestrict_link(link, self.token, provider="realdebrid")

    def resolve_magnet(self, magnet: str, file_idx: Optional[int] = None) -> str:
        import debrid_engine
        return debrid_engine.rd_debrid_magnet(magnet, self.token, file_idx=file_idx, provider="realdebrid")

    def check_instant_availability(self, info_hashes: List[str]) -> Dict[str, bool]:
        import debrid_engine
        return debrid_engine.rd_check_instant_availability(info_hashes, self.token, provider="realdebrid")


class AllDebridAdapter(DebridAdapter):
    name = "alldebrid"

    def __init__(self, token: str):
        self.token = (token or "").strip()

    def get_user_info(self) -> dict:
        import debrid_engine
        return debrid_engine.ad_get_user(self.token)

    def unrestrict_link(self, link: str) -> str:
        import debrid_engine
        return debrid_engine.ad_unrestrict_link(link, self.token)

    def resolve_magnet(self, magnet: str, file_idx: Optional[int] = None) -> str:
        import debrid_engine
        return debrid_engine.ad_debrid_magnet(magnet, self.token, file_idx=file_idx)


class TorBoxAdapter(DebridAdapter):
    name = "torbox"

    def __init__(self, token: str):
        self.token = (token or "").strip()

    def get_user_info(self) -> dict:
        import debrid_engine
        return debrid_engine.torbox_get_user(self.token)

    def unrestrict_link(self, link: str) -> str:
        import debrid_engine
        return debrid_engine.torbox_unrestrict_link(link, self.token)

    def resolve_magnet(self, magnet: str, file_idx: Optional[int] = None) -> str:
        import debrid_engine
        return debrid_engine.torbox_debrid_magnet(magnet, self.token, file_idx=file_idx)


class FakeDebridAdapter(DebridAdapter):
    """Adaptateur de test en mémoire (in-memory test fake)."""
    name = "fake"

    def __init__(self, instant_hashes: List[str] = None):
        self.instant_hashes = set(h.lower() for h in (instant_hashes or []))

    def get_user_info(self) -> dict:
        return {"username": "test_cinephile", "premium": True, "expiration": "2099-01-01"}

    def unrestrict_link(self, link: str) -> str:
        return f"https://mock-cdn.kino.local/stream/{hashlib.md5(link.encode()).hexdigest()}/video.mkv"

    def resolve_magnet(self, magnet: str, file_idx: Optional[int] = None) -> str:
        return f"https://mock-cdn.kino.local/debrided/{hashlib.md5(magnet.encode()).hexdigest()}/video.mkv"

    def check_instant_availability(self, info_hashes: List[str]) -> Dict[str, bool]:
        return {h.lower(): (h.lower() in self.instant_hashes) for h in info_hashes}


def create_debrid_adapter(provider: str = None, token: str = None) -> DebridAdapter:
    """Fabrique l'adaptateur de débridage approprié selon la configuration."""
    cfg = config.load_config()
    prov = (provider or cfg.get("debrid_provider") or "realdebrid").lower().strip()
    
    if prov == "fake":
        return FakeDebridAdapter()

    tok = token or cfg.get("rd_token", "")
    if prov == "alldebrid":
        tok = (cfg.get("provider_tokens") or {}).get("alldebrid") or tok
        return AllDebridAdapter(tok)
    elif prov == "torbox":
        tok = (cfg.get("provider_tokens") or {}).get("torbox") or tok
        return TorBoxAdapter(tok)
    else:
        return RealDebridAdapter(tok)


# ==============================================================================
# INTERFACE PRINCIPALE (THE SEAM)
# ==============================================================================

def get_available_streams(
    query: StreamQuery,
    force_refresh: bool = False,
    debrid_adapter: Optional[DebridAdapter] = None,
) -> List[dict]:
    """
    Découvre, filtre les homonymes, score et évalue l'immédiateté de tous les flux
    correspondant à la StreamQuery. Encapsule le cache L1/L2 de manière étanche.
    """
    if debrid_adapter is None:
        debrid_adapter = create_debrid_adapter()

    has_token = bool(getattr(debrid_adapter, "token", None))
    cache_key = query.cache_key(debrid_adapter.name, has_token)

    def _fetch():
        import debrid_engine
        prov_meta = config.DEBRID_PROVIDERS.get(debrid_adapter.name, config.DEBRID_PROVIDERS.get("realdebrid", {}))
        tio_key = prov_meta.get("torrentio_key", "")
        token = getattr(debrid_adapter, "token", "")
        aliases = []
        if query.title and query.media_type == "movie":
            try:
                canonical_title = (meta_engine.get_media_meta(query.imdb_id, "movie") or {}).get("name", "")
                if canonical_title and canonical_title.casefold() != query.title.casefold():
                    aliases.append(canonical_title)
            except Exception:
                pass

        streams = torrent_engine.search_multi_torrents(
            imdb_id=query.imdb_id,
            media_type=query.media_type,
            season=query.season,
            episode=query.episode,
            token=token,
            tio_key=tio_key,
            provider=debrid_adapter.name,
            runtime_minutes=query.runtime_minutes,
            sort_by=query.sort_by,
            pref_lang=query.pref_lang,
            pref_quality=query.pref_quality,
            hdr_mode=query.hdr_mode,
            source_priority=query.source_priority,
            http_json_fn=config.http_json,
            search_apibay_fn=debrid_engine.search_apibay,
            query_title=query.title,
            release_year=query.release_year,
            title_aliases=aliases,
            absolute_episode=(meta_engine.absolute_episode_number(
                query.imdb_id, query.season, query.episode, allow_network=True)
                if query.media_type in ("series", "anime") and int(query.season or 1) > 1 else None),
        )
        return list(streams or [])

    if force_refresh:
        res = _fetch()
        kino_db.db_cache_set(cache_key, res, ttl_sec=180)
        return res

    return list(config.cached_get(cache_key, 180, _fetch) or [])


def resolve_playable_stream(
    stream: dict,
    query: Optional[StreamQuery] = None,
    debrid_adapter: Optional[DebridAdapter] = None,
) -> PlayableStream:
    """
    Résout un flux sélectionné ou le meilleur flux en une URL débridée directe prête pour le lecteur.
    Lève DebridResolutionError si le débridage échoue.
    """
    if debrid_adapter is None:
        debrid_adapter = create_debrid_adapter()

    # 1. Si le stream possède déjà une URL directe débridée valide
    direct_url = stream.get("url") or stream.get("resolve_url")
    if direct_url and direct_url.startswith("http") and not stream.get("is_fake_mock"):
        return PlayableStream(
            url=direct_url,
            title=stream.get("title") or "KINO Stream",
            quality=(stream.get("qualities") or ["HD"])[0],
            provider=debrid_adapter.name,
            is_instant=True,
            info_hash=stream.get("info_hash") or "",
            details=stream,
        )

    # 2. Débridage via Magnet Link
    magnet = stream.get("magnet")
    if not magnet and stream.get("info_hash"):
        name_enc = urllib.parse.quote(stream.get("title") or "stream")
        magnet = f"magnet:?xt=urn:btih:{stream['info_hash']}&dn={name_enc}"

    if not magnet:
        raise DebridResolutionError("Impossible de débrider ce flux : aucun lien magnet ni info_hash valide.")

    try:
        resolved_url = debrid_adapter.resolve_magnet(magnet, file_idx=stream.get("file_idx"))
        if not resolved_url or not resolved_url.startswith("http"):
            raise DebridResolutionError(f"Le fournisseur {debrid_adapter.name.title()} n'a pas pu générer de lien direct pour ce flux.")

        return PlayableStream(
            url=resolved_url,
            title=stream.get("title") or "KINO Stream",
            quality=(stream.get("qualities") or ["HD"])[0] if stream.get("qualities") else "HD",
            provider=debrid_adapter.name,
            is_instant=True,
            info_hash=stream.get("info_hash") or "",
            details=stream,
        )
    except Exception as e:
        if isinstance(e, DebridResolutionError):
            raise
        raise DebridResolutionError(f"Échec de résolution débridée : {e}") from e
