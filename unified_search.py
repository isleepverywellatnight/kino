"""One search for films and series: results of the catalogue (films, series)
and of the French-title search merged, without duplicates, the closest names
first. Pure logic."""
from cloud_titles import comparable


def match_score(query, meta):
    """3: same name (French or original), 2: name starts with the query,
    1: every word of the query is in the name, 0: anything else."""
    wanted = comparable(query)
    names = [name for name in (comparable(meta.get("name")), comparable(meta.get("name_fr"))) if name]
    if not wanted or not names:
        return 0
    if wanted in names:
        return 3
    if any(name.startswith(wanted) for name in names):
        return 2
    words = wanted.split()
    if any(all(word in name.split() for word in words) for name in names):
        return 1
    return 0


def merge_results(query, movies=(), series=(), french=(), limit=60):
    """Films and series in one list, each with its type, best names first.

    Within the same closeness, each source keeps its own order and the two
    catalogues alternate; French-title matches come first since they are
    checked by name and year.
    """
    seen = set()
    ranked = []
    for order, (source, kind) in enumerate(((french, None), (movies, "movie"), (series, "series"))):
        for position, meta in enumerate(source or []):
            if not isinstance(meta, dict) or not meta.get("id") or meta["id"] in seen:
                continue
            seen.add(meta["id"])
            item = dict(meta)
            item["type"] = "series" if (item.get("type") or kind) == "series" else "movie"
            ranked.append(((-match_score(query, item), position, order), item))
    ranked.sort(key=lambda entry: entry[0])
    return [item for _, item in ranked[:limit]]
