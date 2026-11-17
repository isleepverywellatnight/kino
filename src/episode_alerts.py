"""Which new episodes to announce, and how. Pure logic.

An item of « Nouveaux épisodes » (new_episodes.py) is announced once per
release: a later episode of the same series is a new release. The first check
only records what is already out, so that turning the feature on does not
bring a burst of old episodes. Messages never name the episode: its title can
spoil.
"""
MAX_SEEN = 300
MAX_ALERTS = 3


def alert_key(item):
    kind = item.get("kind") or "followed"
    if kind == "followed":
        return f"{item.get('id')}:followed:{item.get('newest') or item.get('released') or ''}"
    return f"{item.get('id')}:{kind}:s{item.get('season')}"


def _code(item):
    return f"S{int(item.get('season') or 0):02d}E{int(item.get('episode') or 0):02d}"


def message(item):
    """(title, body) in French."""
    name = str(item.get("name") or "Série").strip()
    kind = item.get("kind") or "followed"
    if kind == "premiere":
        return f"Nouvelle série · {name}", f"La saison {item.get('season')} est disponible."
    if kind == "season":
        return f"Nouvelle saison · {name}", f"La saison {item.get('season')} est disponible."
    count = int(item.get("new_count") or 1)
    if count > 1:
        return f"{count} nouveaux épisodes · {name}", f"À partir de {_code(item)}."
    return f"Nouvel épisode · {name}", f"{_code(item)} est disponible."


def pending_alerts(items, seen):
    """(alerts, seen): the items to announce now, and the keys to remember.

    `seen` is None before the first check: everything is recorded, nothing is
    announced. Keys of releases no longer listed are forgotten.
    """
    items = [item for item in items or [] if isinstance(item, dict) and item.get("id")]
    keys = [alert_key(item) for item in items]
    if seen is None:
        return [], keys[:MAX_SEEN]
    known = set(seen)
    alerts = [item for item, key in zip(items, keys) if key not in known]
    remembered = list(dict.fromkeys(keys + [key for key in seen if key in set(keys)]))[:MAX_SEEN]
    return alerts, remembered


def notifications(alerts):
    """What to send: one notification per release, a summary past three."""
    if len(alerts) <= MAX_ALERTS:
        return [{"title": message(item)[0], "body": message(item)[1], "item": item} for item in alerts]
    names = ", ".join(str(item.get("name") or "Série") for item in alerts[:3])
    return [{"title": f"{len(alerts)} séries ont du nouveau", "body": f"{names}…", "item": None}]
