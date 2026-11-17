#!/usr/bin/env python3
"""Thin service layer for app-level HTTP config handlers."""

import hashlib
import threading
import time

import config
import debrid_engine

# The Debrid account is checked over the network: its answer is kept a few
# minutes, so that opening Settings or the start-up does not wait for it again.
ACCOUNT_TTL = 300
# The automatic Cloud clean-up runs at most once an hour, not at each config read.
CLEANUP_EVERY = 3600
_ACCOUNT_CACHE = {}
_ACCOUNT_LOCK = threading.Lock()
_CHECK_LOCK = threading.Lock()
_LAST_CLEANUP = {"at": 0.0}


def _is_authentication_error(error):
    message = str(error).casefold()
    return (
        "erreur api (401)" in message
        or "unauthorized" in message
        or "invalid api key" in message
        or "invalid api token" in message
        or "invalid token" in message
        or "clé alldebrid invalide" in message
        or "clé premiumize invalide" in message
        or "identifiants mega-debrid invalides" in message
    )


def account_status(token, provider):
    """(user_info, status) for this key; a network failure is not remembered."""
    key = (provider, hashlib.sha256(token.encode("utf-8")).hexdigest())

    def cached():
        with _ACCOUNT_LOCK:
            hit = _ACCOUNT_CACHE.get(key)
            return hit if hit and time.monotonic() - hit[0] < ACCOUNT_TTL else None

    hit = cached()
    if hit:
        return hit[1], hit[2]
    # One check at a time: the start-up check and the page's first request share it.
    with _CHECK_LOCK:
        hit = cached()
        if hit:
            return hit[1], hit[2]
        return _check_account(key, token, provider)


def _check_account(key, token, provider):
    now = time.monotonic()
    try:
        user_info = debrid_engine.rd_get_user(token, provider=provider)
        status = "connected" if user_info else "unavailable"
    except Exception as error:
        user_info = None
        status = "invalid" if _is_authentication_error(error) else "unavailable"
    if status != "unavailable":
        with _ACCOUNT_LOCK:
            _ACCOUNT_CACHE.clear()
            _ACCOUNT_CACHE[key] = (now, user_info, status)
    return user_info, status


def warm_account():
    """Start-up: check the account while the window opens."""
    cfg = config.load_config()
    token = cfg.get("rd_token", "")
    if token:
        account_status(token, cfg.get("debrid_provider", "realdebrid"))


def _cleanup_due():
    now = time.monotonic()
    with _ACCOUNT_LOCK:
        if _LAST_CLEANUP["at"] and now - _LAST_CLEANUP["at"] < CLEANUP_EVERY:
            return False
        _LAST_CLEANUP["at"] = now
        return True


def normalize_config_updates(body):
    if not isinstance(body, dict):
        return {}
    return config.sanitize_config_payload(body)


def build_config_response():
    cfg = config.load_config()
    prov = cfg.get("debrid_provider", "realdebrid")
    tok = cfg.get("rd_token", "")
    prov_tokens = cfg.get("provider_tokens") or {}
    user_info = None
    user_status = "unconfigured"
    ret_days = int(cfg.get("rd_retention_days", 0) or 0)
    if tok:
        user_info, user_status = account_status(tok, prov)
        if ret_days > 0 and _cleanup_due():
            threading.Thread(
                target=debrid_engine.rd_auto_cleanup_cloud,
                args=(tok, ret_days, prov),
                daemon=True,
            ).start()
    return {
        "debrid_provider": prov,
        "configured_providers": {k: bool(v) for k, v in prov_tokens.items()},
        "has_token": bool(tok),
        "user": user_info,
        "user_status": user_status,
        "download_dir": cfg.get("download_dir", str(config.DEFAULT_DOWNLOAD_DIR)),
        "player_mode": cfg.get("player_mode", "kino"),
        "pref_lang": cfg.get("pref_lang", "vf"),
        "pref_quality": cfg.get("pref_quality", "4k"),
        "hdr_mode": cfg.get("hdr_mode", "sdr_pref"),
        "source_priority": "quality" if cfg.get("source_priority") == "quality" else "speed",
        "audio_mode": cfg.get("audio_mode", "voice_boost"),
        "rd_retention_days": ret_days,
        "discord_rpc": cfg.get("discord_rpc", True),
        # On by default where it exists (macOS): the built-in web player stays the fallback.
        "native_player": bool(cfg.get("native_player", config.NATIVE_PLAYER is not None)),
        "native_player_available": config.NATIVE_PLAYER is not None,
        # French subtitles when a film or an episode exists only in its original language.
        "auto_fr_subs": cfg.get("auto_fr_subs", True) is not False,
        "episode_notifications": cfg.get("episode_notifications", True) is not False,
        "notifications_available": config.NOTIFIER is not None,
        "discord_client_id": cfg.get("discord_client_id", config.discord_rpc.DEFAULT_CLIENT_ID),
        "has_tmdb_key": bool(cfg.get("tmdb_key")),
    }


def apply_config_updates(body):
    updates = normalize_config_updates(body)
    config.save_config(updates)
    return {"ok": True}
