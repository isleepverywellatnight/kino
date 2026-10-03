#!/usr/bin/env python3
"""
KINO Debrid & Torrent Engine
Gestion des débrideurs (Real-Debrid, AllDebrid, TorBox, Debrid-Link, Premiumize, Mega-Debrid),
recherche et filtrage de torrents (Torrentio, APIBay), décodage Bencode,
détection précise d'épisodes et débridage instantané.
"""

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import kino_db
import torrent_engine
from config import (
    DEBRID_PROVIDERS,
    HEADERS,
    INSTANT_BADGES,
    PORT,
    VIDEO_EXTENSIONS,
    cached_get,
    format_size,
    http_json,
    load_config,
)
from meta_engine import get_media_meta


def _bdecode_slice(data: bytes, idx: int = 0):
    """Décodeur Bencode léger qui conserve la tranche d'octets exacte du dictionnaire 'info'."""
    ch = data[idx : idx + 1]
    if ch == b"i":
        end_i = data.index(b"e", idx + 1)
        return int(data[idx + 1 : end_i]), end_i + 1
    if ch == b"l":
        idx += 1
        arr = []
        while data[idx : idx + 1] != b"e":
            v, idx = _bdecode_slice(data, idx)
            arr.append(v)
        return arr, idx + 1
    if ch == b"d":
        idx += 1
        dct = {}
        while data[idx : idx + 1] != b"e":
            k, idx = _bdecode_slice(data, idx)
            val_start = idx
            v, idx = _bdecode_slice(data, idx)
            val_end = idx
            k_str = k.decode("utf-8", errors="replace") if isinstance(k, bytes) else str(k)
            dct[k_str] = v
            if k_str == "info" and isinstance(v, dict):
                dct["__info_raw__"] = data[val_start:val_end]
        return dct, idx + 1
    if ch and 48 <= ch[0] <= 57:
        colon = data.index(b":", idx)
        length = int(data[idx:colon])
        s_start = colon + 1
        s_end = s_start + length
        return data[s_start:s_end], s_end
    raise ValueError("Format Bencode (.torrent) invalide")


def torrent_file_to_magnet(raw_bytes: bytes):
    """Convertit le contenu binaire d'un fichier .torrent en lien magnet:?xt=urn:btih:..."""
    meta, _ = _bdecode_slice(raw_bytes, 0)
    if not isinstance(meta, dict) or "__info_raw__" not in meta:
        raise RuntimeError("Ce fichier .torrent ne contient pas de section 'info' valide.")
    info_raw = meta["__info_raw__"]
    info_hash = hashlib.sha1(info_raw).hexdigest().lower()
    info_dict = meta.get("info") or {}
    raw_name = info_dict.get("name.utf-8") or info_dict.get("name") or b"Torrent"
    name = raw_name.decode("utf-8", errors="replace") if isinstance(raw_name, bytes) else str(raw_name)
    trackers = []
    ann = meta.get("announce")
    if isinstance(ann, bytes):
        trackers.append(ann.decode("utf-8", errors="ignore"))
    ann_list = meta.get("announce-list")
    if isinstance(ann_list, list):
        for tier in ann_list:
            if isinstance(tier, list):
                for tr in tier:
                    if isinstance(tr, bytes):
                        u = tr.decode("utf-8", errors="ignore")
                        if u and u not in trackers:
                            trackers.append(u)
    tr_qs = "".join(f"&tr={urllib.parse.quote(tr)}" for tr in trackers[:8])
    magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(name)}{tr_qs}"
    return {"magnet": magnet, "name": name, "info_hash": info_hash}


def _parse_rd_iso_age_days(iso_str):
    if not iso_str:
        return 0.0
    try:
        if isinstance(iso_str, (int, float)):
            return max(0.0, (time.time() - float(iso_str)) / 86400.0)
        cleaned = str(iso_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - dt).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def _get_provider_and_token(token=None, provider=None):
    cfg = load_config()
    prov = (provider or cfg.get("debrid_provider") or "realdebrid").strip().lower()
    if prov not in DEBRID_PROVIDERS:
        prov = "realdebrid"
    tok = (token if token is not None else cfg.get("rd_token") or "").strip()
    return prov, tok


def rd_get_downloads(token=None, limit=50, provider=None):
    prov, token = _get_provider_and_token(token, provider)
    if not token:
        return []

    if prov == "alldebrid":
        q = urllib.parse.urlencode({"agent": "KINO", "apikey": token})
        res = http_json(f"https://api.alldebrid.com/v4/magnet/status?{q}")
        magnets = (res.get("data") or {}).get("magnets") or []
        if isinstance(magnets, dict):
            magnets = list(magnets.values())
        items = []
        for m in magnets[:int(limit)]:
            if m.get("statusCode") != 4:
                continue
            mid = str(m.get("id", ""))
            up_ts = m.get("uploadDate") or 0
            gen_date = datetime.fromtimestamp(up_ts, tz=timezone.utc).strftime("%Y-%m-%d") if up_ts else ""
            links = m.get("links") or []
            if links:
                first_l = links[0]
                fname = first_l.get("filename") or m.get("filename") or "Fichier AllDebrid"
                fsize = first_l.get("size") or m.get("size") or 0
                raw_link = first_l.get("link", "")
                dl_api = f"http://127.0.0.1:{PORT}/api/ad-dl?link={urllib.parse.quote(raw_link)}" if raw_link else ""
                items.append({
                    "id": mid,
                    "ids": [mid],
                    "filename": fname,
                    "filesize": format_size(fsize),
                    "download": dl_api,
                    "generated": gen_date,
                    "age_days": round(_parse_rd_iso_age_days(up_ts), 1),
                })
        return items

    if prov == "debridlink":
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json(f"https://debrid-link.com/api/v2/seedbox/list?perPage={int(limit)}", headers=auth)
        torrents = res.get("value") or []
        items = []
        for t in torrents:
            tid = str(t.get("id", ""))
            created = t.get("created") or 0
            gen_date = datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d") if created else ""
            files = t.get("files") or []
            vids = [f for f in files if f.get("downloadUrl")]
            target = vids[0] if vids else (files[0] if files else None)
            if not target or not target.get("downloadUrl"):
                continue
            items.append({
                "id": tid,
                "ids": [tid],
                "filename": target.get("name") or t.get("name") or "Fichier Debrid-Link",
                "filesize": format_size(target.get("size") or t.get("totalSize") or 0),
                "download": target["downloadUrl"],
                "generated": gen_date,
                "age_days": round(_parse_rd_iso_age_days(created), 1),
            })
        return items

    if prov == "torbox":
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json("https://api.torbox.app/v1/api/torrents/mylist?bypass_cache=true", headers=auth)
        torrents = res.get("data") or []
        items = []
        for t in torrents[:int(limit)]:
            if not t.get("download_present"):
                continue
            tid = str(t.get("id", ""))
            files = t.get("files") or []
            if not files:
                continue
            largest = max(files, key=lambda x: x.get("size", 0))
            fid = largest.get("id", 0)
            dl_api = f"http://127.0.0.1:{PORT}/api/torbox-dl?torrent_id={tid}&file_id={fid}"
            created_str = (t.get("created_at") or "")[:10]
            items.append({
                "id": tid,
                "ids": [tid],
                "filename": largest.get("short_name") or largest.get("name") or t.get("name") or "Fichier TorBox",
                "filesize": format_size(largest.get("size") or t.get("size") or 0),
                "download": dl_api,
                "generated": created_str,
                "age_days": round(_parse_rd_iso_age_days(t.get("created_at", "")), 1),
            })
        return items

    if prov == "premiumize":
        q = urllib.parse.urlencode({"apikey": token})
        res = http_json(f"https://www.premiumize.me/api/transfer/list?{q}")
        transfers = res.get("transfers") or []
        items = []
        for t in transfers[:int(limit)]:
            tid = str(t.get("id", ""))
            fname = t.get("name") or "Fichier Premiumize"
            dl = t.get("link") or ""
            if not dl:
                continue
            items.append({
                "id": tid,
                "ids": [tid],
                "filename": fname,
                "filesize": "Cloud PM",
                "download": dl,
                "generated": "",
                "age_days": 0.0,
            })
        return items

    if prov != "realdebrid":
        return []

    auth = {"Authorization": f"Bearer {token}"}
    data = http_json(f"https://api.real-debrid.com/rest/1.0/downloads?limit={int(limit)}", headers=auth)
    if not isinstance(data, list):
        return []
    by_name = {}
    items = []
    for d in data:
        fname = d.get("filename", "")
        dl = d.get("download", "")
        did = d.get("id", "")
        if not fname or not dl or not did:
            continue
        if fname in by_name:
            by_name[fname]["ids"].append(did)
            continue
        entry = {
            "id": did,
            "ids": [did],
            "filename": fname,
            "filesize": format_size(d.get("filesize", 0)),
            "download": dl,
            "generated": (d.get("generated") or "")[:10],
            "age_days": round(_parse_rd_iso_age_days(d.get("generated", "")), 1),
        }
        by_name[fname] = entry
        items.append(entry)
    return items


def rd_delete_downloads(token=None, ids=None, provider=None):
    prov, token = _get_provider_and_token(token, provider)
    if not token or not ids:
        return 0
    if isinstance(ids, str):
        ids = [ids]
    deleted = 0

    if prov == "alldebrid":
        for mid in ids:
            try:
                q = urllib.parse.urlencode({"agent": "KINO", "apikey": token, "id": mid})
                http_json(f"https://api.alldebrid.com/v4/magnet/delete?{q}")
                deleted += 1
            except Exception:
                pass
        return deleted

    if prov == "debridlink":
        auth = {"Authorization": f"Bearer {token}"}
        for tid in ids:
            try:
                http_json(f"https://debrid-link.com/api/v2/seedbox/{tid}/remove", method="DELETE", headers=auth)
                deleted += 1
            except Exception:
                pass
        return deleted

    if prov == "torbox":
        auth = {"Authorization": f"Bearer {token}"}
        for tid in ids:
            try:
                http_json(
                    "https://api.torbox.app/v1/api/torrents/controltorrent",
                    method="POST",
                    json_data={"torrent_id": int(tid) if str(tid).isdigit() else tid, "operation": "delete"},
                    headers=auth,
                )
                deleted += 1
            except Exception:
                pass
        return deleted

    if prov == "premiumize":
        for tid in ids:
            try:
                q = urllib.parse.urlencode({"apikey": token})
                http_json(f"https://www.premiumize.me/api/transfer/delete?{q}", method="POST", data={"id": tid})
                deleted += 1
            except Exception:
                pass
        return deleted

    auth = {"Authorization": f"Bearer {token}"}
    for did in ids:
        if not did:
            continue
        try:
            http_json(f"https://api.real-debrid.com/rest/1.0/downloads/delete/{did}", method="DELETE", headers=auth)
            deleted += 1
        except Exception:
            pass
    return deleted


def rd_cleanup_cloud(token=None, max_age_days=None, provider=None):
    prov, token = _get_provider_and_token(token, provider)
    if not token:
        return {"deleted_downloads": 0, "deleted_torrents": 0}
    if max_age_days is None:
        max_age_days = int(load_config().get("rd_retention_days", 0) or 0)
    if max_age_days == 0:
        return {"deleted_downloads": 0, "deleted_torrents": 0}

    if prov in ("alldebrid", "debridlink", "torbox", "premiumize"):
        items = rd_get_downloads(token, limit=100, provider=prov)
        to_del = [
            it["id"] for it in items
            if max_age_days == -1 or float(it.get("age_days", 0)) >= float(max_age_days)
        ]
        cnt = rd_delete_downloads(token, to_del, provider=prov) if to_del else 0
        return {"deleted_downloads": cnt, "deleted_torrents": 0}

    if prov != "realdebrid":
        return {"deleted_downloads": 0, "deleted_torrents": 0}

    auth = {"Authorization": f"Bearer {token}"}
    del_dl = 0
    del_tor = 0

    try:
        downloads = http_json("https://api.real-debrid.com/rest/1.0/downloads?limit=100", headers=auth)
        if isinstance(downloads, list):
            for d in downloads:
                did = d.get("id")
                if not did:
                    continue
                age = _parse_rd_iso_age_days(d.get("generated", ""))
                if max_age_days == -1 or age >= float(max_age_days):
                    try:
                        http_json(f"https://api.real-debrid.com/rest/1.0/downloads/delete/{did}", method="DELETE", headers=auth)
                        del_dl += 1
                    except Exception:
                        pass
    except Exception:
        pass

    try:
        torrents = http_json("https://api.real-debrid.com/rest/1.0/torrents?limit=100", headers=auth)
        if isinstance(torrents, list):
            for t in torrents:
                tid = t.get("id")
                if not tid:
                    continue
                age = _parse_rd_iso_age_days(t.get("added", ""))
                if max_age_days == -1 or age >= float(max_age_days):
                    try:
                        http_json(f"https://api.real-debrid.com/rest/1.0/torrents/delete/{tid}", method="DELETE", headers=auth)
                        del_tor += 1
                    except Exception:
                        pass
    except Exception:
        pass

    return {"deleted_downloads": del_dl, "deleted_torrents": del_tor}


def parse_torrent_tags(text):
    p = torrent_engine.parse_release_details(text)
    return p.get("display_badges", []), p.get("langs", [])


def clean_meta_text(text):
    return torrent_engine.clean_meta_text(text)


def _parse_stremio_streams(streams, default_source="Torrentio"):
    parsed = []
    for s in streams or []:
        raw_title = s.get("title") or s.get("description") or ""
        source_tag = (s.get("name") or default_source).replace("\n", " ").strip()
        lines = [line.strip() for line in raw_title.split("\n") if line.strip()]
        release_name = lines[0] if lines else "Stream"
        meta_info = clean_meta_text(" • ".join(lines[1:])) if len(lines) > 1 else ""

        seeders = int(s.get("seed") or s.get("seeders") or 0)
        if not seeders:
            m_seed = re.search(r"👤\s*(\d+)", raw_title) or re.search(r"\b(\d+)\s*(?:seeders?|seeds?|pairs?)\b", raw_title, re.IGNORECASE)
            seeders = int(m_seed.group(1)) if m_seed else 0

        size_bytes = int(s.get("sizebytes") or s.get("size") or 0)
        size_gb = 0.0
        size_str = ""
        if size_bytes > 0:
            size_gb = round(size_bytes / (1024.0 * 1024.0 * 1024.0), 2)
            size_str = f"{size_gb} GB"
        else:
            m_gb = re.search(r"💾\s*([\d\.]+)\s*GB", raw_title, re.IGNORECASE) or re.search(r"([\d\.]+)\s*(?:GB|GiB)", raw_title, re.IGNORECASE)
            m_mb = re.search(r"💾\s*([\d\.]+)\s*MB", raw_title, re.IGNORECASE) or re.search(r"([\d\.]+)\s*(?:MB|MiB)", raw_title, re.IGNORECASE)
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
            continue

        st_lower = source_tag.lower()
        is_instant = any(f"[{b}]" in st_lower for b in ("rd+", "ad+", "tb+", "dl+", "pm+", "oc+", "ed+"))
        qualities, langs = parse_torrent_tags(f"{source_tag} {raw_title}")
        magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(release_name)}" if info_hash else resolve_url

        display_meta = meta_info
        if display_meta.startswith(f"{default_source} • "):
            display_meta = display_meta[len(f"{default_source} • "):]
        elif display_meta == default_source:
            display_meta = ""

        parsed.append({
            "source": default_source,
            "title": release_name,
            "meta": display_meta,
            "qualities": qualities,
            "langs": langs,
            "magnet": magnet,
            "resolve_url": resolve_url,
            "is_instant": is_instant,
            "info_hash": info_hash,
            "file_idx": s.get("fileIdx"),
            "seeders": seeders,
            "size_gb": size_gb,
            "size_str": size_str,
        })
    return parsed


def is_plausible_torrent_size(t, media_type="movie", runtime_minutes=None):
    return torrent_engine.is_plausible_torrent_size(t, media_type=media_type, runtime_minutes=runtime_minutes)


def search_torrentio(imdb_id, media_type="movie", season=1, episode=1, rd_token=None, provider=None, runtime_minutes=None, sort_by="score", query_title="", release_year=""):
    prov, token = _get_provider_and_token(rd_token, provider)
    prov_meta = DEBRID_PROVIDERS.get(prov, DEBRID_PROVIDERS["realdebrid"])
    tio_key = prov_meta.get("torrentio_key", "")

    cfg = load_config()
    pref_lang = cfg.get("pref_lang", "vf")
    pref_quality = cfg.get("pref_quality", "4k")
    hdr_mode = cfg.get("hdr_mode", "sdr_pref")

    clean_q = query_title.strip() if query_title else ""
    clean_yr = str(release_year).strip() if release_year else ""

    if imdb_id and (not clean_q or not clean_yr or runtime_minutes is None):
        try:
            m_info = get_media_meta(imdb_id, media_type if media_type in ("movie", "series", "anime") else "movie")
            if not clean_q:
                clean_q = (m_info or {}).get("name", "").strip()
            if not clean_yr:
                clean_yr = str((m_info or {}).get("year") or (m_info or {}).get("releaseInfo") or "").strip()
            if runtime_minutes is None and media_type == "movie":
                rt_str = str((m_info or {}).get("runtime") or "")
                m_rt = re.search(r"(\d+)", rt_str)
                if m_rt:
                    runtime_minutes = int(m_rt.group(1))
        except Exception:
            pass

    cache_key = f"multi_engine_v4:{prov}:{media_type}:{imdb_id}:{season}:{episode}:{bool(token)}:{sort_by}:{clean_q}:{clean_yr}"

    def _fetch():
        return torrent_engine.search_multi_torrents(
            imdb_id=imdb_id,
            media_type=media_type,
            season=season,
            episode=episode,
            token=token,
            tio_key=tio_key,
            provider=prov,
            runtime_minutes=runtime_minutes,
            sort_by=sort_by,
            pref_lang=pref_lang,
            pref_quality=pref_quality,
            hdr_mode=hdr_mode,
            http_json_fn=http_json,
            search_apibay_fn=search_apibay,
            query_title=clean_q,
            release_year=clean_yr,
        )

    res = list(cached_get(cache_key, 180, _fetch) or [])
    return res


def search_apibay(query):
    encoded = urllib.parse.quote(query)
    url = f"https://apibay.org/q.php?q={encoded}&cat=200"
    data = http_json(url)
    results = []
    if not isinstance(data, list):
        return results
    for item in data:
        info_hash = item.get("info_hash")
        name = item.get("name")
        if not info_hash or info_hash == "0000000000000000000000000000000000000000":
            continue
        seeders = int(item.get("seeders", 0) or 0)
        size_bytes = int(item.get("size", 0) or 0)
        size_gb = round(size_bytes / (1024.0 * 1024.0 * 1024.0), 2)
        size_str = format_size(size_bytes)
        details = torrent_engine.parse_release_details(name)
        magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(name)}"
        t_entry = {
            "source": "APIBay",
            "title": name,
            "meta": f"{seeders} seeders • {size_str}",
            "qualities": details["display_badges"],
            "langs": details["langs"],
            "magnet": magnet,
            "resolve_url": "",
            "is_instant": False,
            "info_hash": info_hash,
            "file_idx": None,
            "seeders": seeders,
            "size_gb": size_gb,
            "size_str": size_str,
            "parsed_details": details,
        }
        if torrent_engine.is_plausible_torrent_size(t_entry, media_type="movie"):
            results.append(t_entry)
    return results


def score_torrent_for_one_click(t):
    cfg = load_config()
    pref_lang = cfg.get("pref_lang", "vf")
    pref_quality = cfg.get("pref_quality", "4k")
    hdr_mode = cfg.get("hdr_mode", "sdr_pref")
    return torrent_engine.score_torrent(t, pref_lang=pref_lang, pref_quality=pref_quality, hdr_mode=hdr_mode)


def rd_get_user(token=None, provider=None):
    prov, token = _get_provider_and_token(token, provider)
    if not token:
        return None
    pmeta = DEBRID_PROVIDERS.get(prov, DEBRID_PROVIDERS["realdebrid"])

    if prov == "alldebrid":
        q = urllib.parse.urlencode({"agent": "KINO", "apikey": token})
        res = http_json(f"https://api.alldebrid.com/v4/user?{q}")
        if res.get("status") != "success":
            raise RuntimeError((res.get("error") or {}).get("message", "Clé AllDebrid invalide"))
        u = (res.get("data") or {}).get("user") or {}
        rem = max(0, int((u.get("premiumUntil") or 0) - time.time())) if u.get("isPremium") else 0
        return {"username": u.get("username", "AllDebrid"), "premium": rem, "provider": pmeta["name"], "short": pmeta["short"]}

    if prov == "debridlink":
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json("https://debrid-link.com/api/v2/account/infos", headers=auth)
        u = res.get("value") or {}
        return {
            "username": u.get("username") or u.get("email") or "Debrid-Link",
            "premium": int(u.get("premiumLeft") or 0),
            "provider": pmeta["name"],
            "short": pmeta["short"],
        }

    if prov == "torbox":
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json("https://api.torbox.app/v1/api/user/me", headers=auth)
        u = res.get("data") or {}
        exp_str = u.get("premium_expires_at") or ""
        rem = 0
        if exp_str:
            try:
                dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                rem = max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
            except Exception:
                rem = 86400 * 30 if u.get("plan", 0) > 0 else 0
        elif u.get("plan", 0) > 0:
            rem = 86400 * 30
        uname = (u.get("email") or "TorBox").split("@")[0]
        return {"username": uname, "premium": rem, "provider": pmeta["name"], "short": pmeta["short"]}

    if prov == "premiumize":
        q = urllib.parse.urlencode({"apikey": token})
        res = http_json(f"https://www.premiumize.me/api/account/info?{q}")
        if res.get("status") != "success":
            raise RuntimeError(res.get("message", "Clé Premiumize invalide"))
        rem = max(0, int((res.get("premium_until") or 0) - time.time()))
        return {
            "username": str(res.get("customer_id") or "Premiumize"),
            "premium": rem,
            "provider": pmeta["name"],
            "short": pmeta["short"],
        }

    if prov == "megadebrid":
        if ":" in token:
            login, pw = token.split(":", 1)
            q = urllib.parse.urlencode({"form": "connexion", "login": login, "password": pw})
            res = http_json(f"https://www.mega-debrid.eu/index.php?{q}")
            if res.get("response_code") != "ok":
                raise RuntimeError(res.get("response_text", "Identifiants Mega-Debrid invalides"))
            rem = max(0, int(res.get("vip_end", 0)) - int(time.time())) if res.get("vip_end") else 86400
            return {"username": login, "premium": rem, "provider": pmeta["name"], "short": pmeta["short"]}
        return {"username": "Mega-Debrid", "premium": 86400 * 30, "provider": pmeta["name"], "short": pmeta["short"]}

    auth = {"Authorization": f"Bearer {token}"}
    u = http_json("https://api.real-debrid.com/rest/1.0/user", headers=auth)
    u["provider"] = pmeta["name"]
    u["short"] = pmeta["short"]
    return u


def resolve_torrentio_rd_url(resolve_url):
    req_headers = dict(HEADERS)
    req_headers["Range"] = "bytes=0-0"
    req = urllib.request.Request(resolve_url, headers=req_headers, method="GET")
    with urllib.request.urlopen(req, timeout=25) as resp:
        final_url = resp.geturl()
        content_range = resp.headers.get("Content-Range", "")
        total_bytes = 0
        if "/" in content_range:
            try:
                total_bytes = int(content_range.split("/")[-1])
            except Exception:
                total_bytes = 0
        if not total_bytes:
            total_bytes = int(resp.headers.get("Content-Length", 0))

    bad_Placeholders = ("downloading.mp4", "cached.mp4", "error.mp4", "non_debrid.mp4")
    if "torrentio.strem.fun" in final_url or any(final_url.lower().endswith(p) for p in bad_Placeholders):
        raise RuntimeError("Flux non disponible en cache instantané sur votre débrideur.")

    prov, _ = _get_provider_and_token()
    badge = DEBRID_PROVIDERS.get(prov, DEBRID_PROVIDERS["realdebrid"])["badge"]
    filename = urllib.parse.unquote(final_url.split("/")[-1].split("?")[0])
    return {
        "ready": True,
        "torrent_id": "instant_cache",
        "status": "downloaded",
        "progress": 100,
        "files": [{
            "filename": filename,
            "filesize": format_size(total_bytes) if total_bytes > 1024 else f"Prêt (Flux Direct {badge})",
            "filesize_bytes": total_bytes,
            "download": final_url,
            "is_target_ep": True,
        }],
    }


def is_target_episode_file(filename: str, season: int = None, episode: int = None, absolute_ep: int = None) -> bool:
    if not filename or (season is None and episode is None and absolute_ep is None):
        return True

    fname = filename.strip()
    s_num = int(season) if season else 1
    e_num = int(episode) if episode else 1
    abs_num = int(absolute_ep) if absolute_ep else None

    if re.search(r"[\s\-_\[(](?:NC)?(?:OP|ED|PV|TRAILER|SAMPLE|PREVIEW|MENU)\b(?:\d+)?", fname, re.IGNORECASE):
        if not re.search(r"\bOne\s+Piece\b", fname, re.IGNORECASE) or re.search(r"[\s\-_\[(](?:NCOP|NCED|PV|SAMPLE)\b", fname, re.IGNORECASE):
            return False

    std_pattern = rf"(?:s0?{s_num}[\.\-_ ]?e0?{e_num}\b|\b{s_num}x0?{e_num}\b)"
    if re.search(std_pattern, fname, re.IGNORECASE):
        return True

    s_marker = rf"(?:s0?{s_num}|season\s*0?{s_num}|{s_num}(?:nd|rd|th|st)\s*season|part\s*0?{s_num})\b"
    if re.search(s_marker, fname, re.IGNORECASE):
        ep_after_s = re.search(s_marker + r"[\s\-_:]*(?:ep(?:isode)?\.?\s*|e)?0*" + str(e_num) + r"(?:v\d+)?(?:[\s\-_\]\).]|$)", fname, re.IGNORECASE)
        if ep_after_s:
            return True

    cleaned = fname
    cleaned = re.sub(r"\[[0-9a-fA-F]{6,8}\]", "", cleaned)
    cleaned = re.sub(r"\b(?:2160p|1080p|720p|480p|4k|uhd|fhd|hd)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:x264|x265|h264|h265|hevc|av1|10bit|8bit)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:5\.1|7\.1|2\.0)\b", "", cleaned)
    cleaned = re.sub(r"\b(?:19|20)\d{2}\b", "", cleaned)
    cleaned = re.sub(r"\b(?:aac|flac|ac3|eac3|dts|mp3)\b", "", cleaned, flags=re.IGNORECASE)

    other_season = re.search(r"\b(?:s|season|part)\s*0?(\d+)\b", cleaned, re.IGNORECASE)
    if other_season:
        detected_s = int(other_season.group(1))
        if detected_s != s_num:
            return False

    target_numbers = [e_num]
    if abs_num and abs_num != e_num:
        target_numbers.append(abs_num)

    for target_n in target_numbers:
        pats = [
            rf"[\s\-_]0*{target_n}(?:v\d+)?[\s\-_\.\]\)]",
            rf"\[0*{target_n}(?:v\d+)?\]",
            rf"\(0*{target_n}(?:v\d+)?\)",
            rf"\b(?:ep(?:isode)?\.?|e|#)\s*0*{target_n}(?:v\d+)?\b",
        ]
        for p in pats:
            if re.search(p, cleaned, re.IGNORECASE):
                return True

    return False


class EpisodeMatcher:
    def __init__(self, season=None, episode=None, absolute_ep=None):
        self.season = int(season) if season is not None else None
        self.episode = int(episode) if episode is not None else None
        self.absolute_ep = int(absolute_ep) if absolute_ep is not None else None

    def search(self, filename: str) -> bool:
        return is_target_episode_file(filename, self.season, self.episode, self.absolute_ep)


def _build_ep_pattern(season, episode, absolute_ep=None):
    if season is not None or episode is not None or absolute_ep is not None:
        return EpisodeMatcher(season, episode, absolute_ep)
    return None


def _alldebrid_unlock_magnet(token, m_info, season=None, episode=None):
    ep_pattern = _build_ep_pattern(season, episode)
    links = m_info.get("links") or []
    candidates = []
    for l_item in links:
        fname = l_item.get("filename") or "video.mkv"
        fsize = int(l_item.get("size") or 0)
        raw_link = l_item.get("link") or ""
        if not raw_link:
            continue
        is_vid = fname.lower().endswith(VIDEO_EXTENSIONS) or fsize > 25 * 1024 * 1024
        if not is_vid and len(links) > 1:
            continue
        is_target = bool(ep_pattern and ep_pattern.search(fname))
        candidates.append({
            "filename": fname,
            "filesize": format_size(fsize),
            "filesize_bytes": fsize,
            "raw_link": raw_link,
            "is_target_ep": is_target,
        })
    candidates.sort(key=lambda x: (not x["is_target_ep"], x["filename"]))
    unrestricted = []
    for c in candidates[:12]:
        try:
            q = urllib.parse.urlencode({"agent": "KINO", "apikey": token, "link": c["raw_link"]})
            u = http_json(f"https://api.alldebrid.com/v4/link/unlock?{q}")
            dl = ((u.get("data") or {}).get("link")) or ""
            if dl:
                unrestricted.append({
                    "filename": c["filename"],
                    "filesize": c["filesize"],
                    "filesize_bytes": c["filesize_bytes"],
                    "download": dl,
                    "is_target_ep": c["is_target_ep"],
                })
        except Exception:
            pass
    return {
        "ready": True,
        "torrent_id": f"ad:{m_info.get('id')}",
        "status": "downloaded",
        "progress": 100,
        "files": unrestricted,
    }


def rd_debrid_magnet(token, magnet, season=None, episode=None, resolve_url=None, provider=None):
    prov, token = _get_provider_and_token(token, provider)
    pmeta = DEBRID_PROVIDERS.get(prov, DEBRID_PROVIDERS["realdebrid"])
    if not token:
        raise RuntimeError(f"Clé API {pmeta['name']} manquante. Cliquez sur 'Config' en haut à droite.")

    target_resolve = resolve_url or (magnet if magnet.startswith("http") else None)
    if target_resolve:
        try:
            return resolve_torrentio_rd_url(target_resolve)
        except Exception:
            if not magnet.startswith("magnet:"):
                raise

    if prov == "alldebrid":
        q = urllib.parse.urlencode({"agent": "KINO", "apikey": token})
        up = http_json(f"https://api.alldebrid.com/v4/magnet/upload?{q}", method="POST", data={"magnets[]": magnet})
        magnets = ((up.get("data") or {}).get("magnets")) or []
        if not magnets:
            raise RuntimeError("AllDebrid n'a pas pu ajouter ce magnet.")
        mid = magnets[0].get("id")
        for _ in range(6):
            st = http_json(f"https://api.alldebrid.com/v4/magnet/status?{q}&id={mid}")
            m_info = ((st.get("data") or {}).get("magnets")) or {}
            if isinstance(m_info, list):
                m_info = m_info[0] if m_info else {}
            if m_info.get("statusCode") == 4:
                return _alldebrid_unlock_magnet(token, m_info, season, episode)
            if m_info.get("statusCode", 0) > 4:
                raise RuntimeError(f"AllDebrid erreur magnet : {m_info.get('status')}")
            time.sleep(1.0)
        return {
            "ready": False,
            "torrent_id": f"ad:{mid}",
            "status": m_info.get("status", "Téléchargement Cloud AllDebrid"),
            "progress": int(
                (m_info.get("downloaded", 0) / max(1, m_info.get("size", 1))) * 100
            ) if m_info.get("size") else 0,
            "speed": format_size(m_info.get("downloadSpeed", 0)) + "/s",
            "seeders": m_info.get("seeders", 0),
            "files": [],
        }

    if prov == "debridlink":
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json("https://debrid-link.com/api/v2/seedbox/add", method="POST", data={"url": magnet, "async": "true"}, headers=auth)
        val = res.get("value") or {}
        files = val.get("files") or []
        ep_pattern = _build_ep_pattern(season, episode)
        unrestricted = []
        for f in files:
            dl = f.get("downloadUrl")
            fname = f.get("name") or "video.mkv"
            if dl and f.get("downloadPercent") == 100:
                unrestricted.append({
                    "filename": fname,
                    "filesize": format_size(f.get("size", 0)),
                    "filesize_bytes": f.get("size", 0),
                    "download": dl,
                    "is_target_ep": bool(ep_pattern and ep_pattern.search(fname)),
                })
        if unrestricted:
            unrestricted.sort(key=lambda x: (not x["is_target_ep"], x["filename"]))
            return {"ready": True, "torrent_id": f"dl:{val.get('id')}", "status": "downloaded", "progress": 100, "files": unrestricted}
        return {
            "ready": False,
            "torrent_id": f"dl:{val.get('id')}",
            "status": "Mise en cache Debrid-Link",
            "progress": val.get("downloadPercent", 0),
            "speed": format_size(val.get("downloadSpeed", 0)) + "/s",
            "seeders": val.get("peersConnected", 0),
            "files": [],
        }

    if prov == "premiumize":
        q = urllib.parse.urlencode({"apikey": token})
        res = http_json(f"https://www.premiumize.me/api/transfer/directdl?{q}", method="POST", data={"src": magnet})
        content = res.get("content") or []
        ep_pattern = _build_ep_pattern(season, episode)
        unrestricted = []
        for c in content:
            dl = c.get("stream_link") or c.get("link")
            fname = (c.get("path") or "video.mkv").split("/")[-1]
            if dl and fname.lower().endswith(VIDEO_EXTENSIONS):
                unrestricted.append({
                    "filename": fname,
                    "filesize": format_size(c.get("size", 0)),
                    "filesize_bytes": c.get("size", 0),
                    "download": dl,
                    "is_target_ep": bool(ep_pattern and ep_pattern.search(fname)),
                })
        if unrestricted:
            unrestricted.sort(key=lambda x: (not x["is_target_ep"], x["filename"]))
            return {"ready": True, "torrent_id": "pm_direct", "status": "downloaded", "progress": 100, "files": unrestricted}
        raise RuntimeError("Ce magnet n'est pas encore en cache instantané sur Premiumize.")

    if prov == "torbox":
        auth = {"Authorization": f"Bearer {token}"}
        added = http_json("https://api.torbox.app/v1/api/torrents/createtorrent", method="POST", data={"magnet": magnet}, headers=auth)
        tid = (added.get("data") or {}).get("torrent_id")
        if not tid:
            raise RuntimeError(added.get("detail") or "TorBox n'a pas pu ajouter ce magnet.")
        return rd_check_torrent(token, f"tb:{tid}", season, episode, provider="torbox")

    if prov == "megadebrid":
        md_tok = token
        if ":" in token:
            login, pw = token.split(":", 1)
            q = urllib.parse.urlencode({"form": "connexion", "login": login, "password": pw})
            c_res = http_json(f"https://www.mega-debrid.eu/index.php?{q}")
            md_tok = c_res.get("token", "")
        if not magnet.startswith("magnet:"):
            q = urllib.parse.urlencode({"form": "debrid", "token": md_tok})
            d_res = http_json(f"https://www.mega-debrid.eu/index.php?{q}", method="POST", data={"link": magnet})
            dl = d_res.get("debridLink", "")
            if dl:
                fname = d_res.get("filename") or urllib.parse.unquote(dl.split("/")[-1].split("?")[0])
                return {
                    "ready": True,
                    "torrent_id": "md_direct",
                    "status": "downloaded",
                    "progress": 100,
                    "files": [{"filename": fname, "filesize": "Prêt (Mega-Debrid)", "filesize_bytes": 0, "download": dl, "is_target_ep": True}],
                }
        raise RuntimeError("Mega-Debrid débride les liens hébergeurs directs. Pour le streaming torrent P2P instantané, utilisez Real-Debrid, AllDebrid, TorBox, Debrid-Link ou Premiumize.")

    auth = {"Authorization": f"Bearer {token}"}
    base = "https://api.real-debrid.com/rest/1.0"

    added = http_json(f"{base}/torrents/addMagnet", method="POST", data={"magnet": magnet}, headers=auth)
    torrent_id = added["id"]

    info = http_json(f"{base}/torrents/info/{torrent_id}", headers=auth)
    files = info.get("files", [])

    video_files = [
        f for f in files
        if f.get("path", "").lower().endswith(VIDEO_EXTENSIONS) and f.get("bytes", 0) > 25 * 1024 * 1024
    ]

    selected_ids = []
    if video_files:
        selected_ids = [str(f["id"]) for f in video_files]
    elif files:
        largest = max(files, key=lambda x: x.get("bytes", 0))
        selected_ids = [str(largest["id"])]

    selection = ",".join(selected_ids) if selected_ids else "all"
    if files:
        http_json(f"{base}/torrents/selectFiles/{torrent_id}", method="POST", data={"files": selection}, headers=auth)

    for _ in range(8):
        info = http_json(f"{base}/torrents/info/{torrent_id}", headers=auth)
        status = info.get("status")
        if status == "downloaded":
            break
        if status in ("error", "magnet_error", "virus", "dead"):
            raise RuntimeError(f"Real-Debrid a refusé ce torrent (statut : {status})")
        time.sleep(1.0)

    if info.get("status") != "downloaded":
        return {
            "ready": False,
            "torrent_id": torrent_id,
            "status": info.get("status"),
            "progress": info.get("progress", 0),
            "speed": format_size(info.get("speed", 0)) + "/s",
            "seeders": info.get("seeders", 0),
            "files": [],
        }

    return unrestrict_torrent_links(token, info, season, episode)


def rd_check_torrent(token, torrent_id, season=None, episode=None, provider=None):
    prov, token = _get_provider_and_token(token, provider)
    tid_str = str(torrent_id or "")

    if tid_str.startswith("ad:") or prov == "alldebrid":
        mid = tid_str.split(":", 1)[-1]
        q = urllib.parse.urlencode({"agent": "KINO", "apikey": token, "id": mid})
        st = http_json(f"https://api.alldebrid.com/v4/magnet/status?{q}")
        m_info = ((st.get("data") or {}).get("magnets")) or {}
        if isinstance(m_info, list):
            m_info = m_info[0] if m_info else {}
        if m_info.get("statusCode") == 4:
            return _alldebrid_unlock_magnet(token, m_info, season, episode)
        return {
            "ready": False,
            "torrent_id": f"ad:{mid}",
            "status": m_info.get("status", "Téléchargement AllDebrid"),
            "progress": int((m_info.get("downloaded", 0) / max(1, m_info.get("size", 1))) * 100) if m_info.get("size") else 0,
            "speed": format_size(m_info.get("downloadSpeed", 0)) + "/s",
            "seeders": m_info.get("seeders", 0),
            "files": [],
        }

    if tid_str.startswith("tb:") or prov == "torbox":
        raw_tid = tid_str.split(":", 1)[-1]
        auth = {"Authorization": f"Bearer {token}"}
        res = http_json(f"https://api.torbox.app/v1/api/torrents/mylist?bypass_cache=true&id={raw_tid}", headers=auth)
        t_info = res.get("data") or {}
        if isinstance(t_info, list):
            t_info = t_info[0] if t_info else {}
        if t_info.get("download_present") and t_info.get("files"):
            ep_pattern = _build_ep_pattern(season, episode)
            unrestricted = []
            for f in t_info.get("files") or []:
                fname = f.get("short_name") or f.get("name") or "video.mkv"
                if not fname.lower().endswith(VIDEO_EXTENSIONS):
                    continue
                fid = f.get("id", 0)
                dl_res = http_json(f"https://api.torbox.app/v1/api/torrents/requestdl?token={token}&torrent_id={raw_tid}&file_id={fid}", headers=auth)
                dl_url = dl_res.get("data") or ""
                if dl_url:
                    unrestricted.append({
                        "filename": fname,
                        "filesize": format_size(f.get("size", 0)),
                        "filesize_bytes": f.get("size", 0),
                        "download": dl_url,
                        "is_target_ep": bool(ep_pattern and ep_pattern.search(fname)),
                    })
            unrestricted.sort(key=lambda x: (not x["is_target_ep"], x["filename"]))
            return {"ready": True, "torrent_id": f"tb:{raw_tid}", "status": "downloaded", "progress": 100, "files": unrestricted}
        return {
            "ready": False,
            "torrent_id": f"tb:{raw_tid}",
            "status": t_info.get("download_state", "Téléchargement TorBox"),
            "progress": int((t_info.get("progress") or 0) * 100),
            "speed": format_size(t_info.get("download_speed", 0)) + "/s",
            "seeders": t_info.get("seeds", 0),
            "files": [],
        }

    auth = {"Authorization": f"Bearer {token}"}
    base = "https://api.real-debrid.com/rest/1.0"
    info = http_json(f"{base}/torrents/info/{torrent_id}", headers=auth)
    if info.get("status") != "downloaded":
        return {
            "ready": False,
            "torrent_id": torrent_id,
            "status": info.get("status"),
            "progress": info.get("progress", 0),
            "speed": format_size(info.get("speed", 0)) + "/s",
            "seeders": info.get("seeders", 0),
            "files": [],
        }
    return unrestrict_torrent_links(token, info, season, episode)


def unrestrict_torrent_links(token, info, season=None, episode=None):
    auth = {"Authorization": f"Bearer {token}"}
    base = "https://api.real-debrid.com/rest/1.0"
    unrestricted = []

    ep_pattern = _build_ep_pattern(season, episode)

    for link in info.get("links", []):
        try:
            u = http_json(f"{base}/unrestrict/link", method="POST", data={"link": link}, headers=auth)
            fname = u.get("filename", "video.mkv")
            is_target_ep = bool(ep_pattern and ep_pattern.search(fname))
            unrestricted.append({
                "filename": fname,
                "filesize": format_size(u.get("filesize", 0)),
                "filesize_bytes": u.get("size", 0),
                "download": u.get("download"),
                "is_target_ep": is_target_ep,
            })
        except Exception:
            pass

    unrestricted.sort(key=lambda x: (not x["is_target_ep"], x["filename"]))

    return {
        "ready": True,
        "torrent_id": info.get("id"),
        "status": "downloaded",
        "progress": 100,
        "files": unrestricted,
    }
