"""KINO's own suggestions: « Pour vous », « Si vous avez aimé » and
« Vous aimerez aussi », computed on this computer from what the user watched,
rated and set aside. No account, no key, nothing about the user leaves the Mac:
KINO only reads public film records (Cinemeta), as it already does.

1. The pool: a few thousand films and series, read from Cinemeta's public
   catalogues (popular, best rated, by genre, by year) once a month, kept with
   what describes them: genres, directors, writers, cast, country, decade and
   the words of their summary.
2. Each title becomes a weighted vector of those traits (rare traits weigh
   more: two films by the same director say more than two dramas).
3. The taste profile adds up the titles the user watched or rated, with a
   weight from their rating; low ratings and « Pas pour moi » count against.
4. A suggestion is a title close to the profile (or to one title), not seen,
   with a little weight for its rating, chosen so that the list is varied, and
   it says why, from the titles the user liked.

Pure functions on lists and dicts; the network and the database are passed in.
"""
import json
import math
import re
import threading
import time
import unicodedata

POOL_MAX_AGE = 30 * 86400
DESCRIPTION_WORDS = 25
# Tuned on the user's real history (2026-11-12): people first, but genres and themes count.
GROUP_WEIGHT = {"d": 2.2, "w": 1.6, "c": 1.0, "g": 1.6, "k": 1.4, "dec": 0.9, "co": 0.8}
# One person (a director, an actor) leads at most this many titles of a list.
PER_PERSON = 3
CAST_KEPT = 5
QUALITY_WEIGHT = 0.12
DIVERSITY = 0.25  # MMR: share given to « different from what is already chosen »

MOVIE_GENRES = ["Action", "Adventure", "Animation", "Biography", "Comedy", "Crime", "Documentary", "Drama", "Family",
                "Fantasy", "History", "Horror", "Mystery", "Romance", "Sci-Fi", "Sport", "Thriller", "War", "Western"]
SERIES_GENRES = MOVIE_GENRES + ["Reality-TV"]
GENRE_FR = {"Action": "action", "Adventure": "aventure", "Animation": "animation", "Biography": "biopic",
            "Comedy": "comédie", "Crime": "policier", "Documentary": "documentaire", "Drama": "drame",
            "Family": "familial", "Fantasy": "fantastique", "History": "histoire", "Horror": "horreur",
            "Mystery": "mystère", "Romance": "romance", "Sci-Fi": "science-fiction", "Sport": "sport",
            "Thriller": "thriller", "War": "guerre", "Western": "western", "Music": "musique"}

STOPWORDS = set("""
about above after again against all also among another around away back because been before being below between
both came come could does doing down during each even ever every find finds first from further gets give goes
going have having into just last life like lives made make makes many more most much must must never new next
only other over own same seen should since some still such take takes than that their them then there these they
thing this those though through time together toward under until upon very want wants well were what when where
which while whose with without world would year years young your story film movie series season help helps begins
becomes find found discovers soon must must left right town city day days night home family friend friends
""".split())


# --- 1. The pool -------------------------------------------------------------
def pool_pages():
    """(type, catalogue, genre, skip) of the pages read: about 200 requests."""
    pages = [("movie", "top", "", skip) for skip in range(0, 1000, 50)]
    pages += [("movie", "imdbRating", "", skip) for skip in range(0, 500, 50)]
    pages += [("movie", "top", genre, skip) for genre in MOVIE_GENRES for skip in (0, 50, 100)]
    pages += [("movie", "year", str(year), skip) for year in range(time.gmtime().tm_year, 1999, -1) for skip in (0, 50)]
    pages += [("series", "top", "", skip) for skip in range(0, 500, 50)]
    pages += [("series", "imdbRating", "", skip) for skip in range(0, 250, 50)]
    pages += [("series", "top", genre, skip) for genre in SERIES_GENRES for skip in (0, 50)]
    return pages


def page_url(kind, catalog, genre, skip):
    extra = "&".join(part for part in (f"genre={genre}" if genre else "", f"skip={skip}" if skip else "") if part)
    return f"https://v3-cinemeta.strem.io/catalog/{kind}/{catalog}{'/' + extra if extra else ''}.json"


def _names(value, limit=None):
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    names = [str(name).strip() for name in value or [] if str(name).strip()]
    return names[:limit] if limit else names


def compact(meta, kind):
    """What the suggestions need of a public record, or None."""
    imdb_id = str(meta.get("imdb_id") or meta.get("id") or "")
    if not re.fullmatch(r"tt\d{1,12}", imdb_id) or not meta.get("name"):
        return None
    year = str(meta.get("year") or meta.get("releaseInfo") or "")[:4]
    try:
        rating = float(meta.get("imdbRating") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    try:
        popularity = float(meta.get("popularity") or 0)
    except (TypeError, ValueError):
        popularity = 0.0
    return {
        "id": imdb_id, "type": "series" if kind == "series" else "movie", "name": str(meta["name"])[:200],
        "year": year if year.isdigit() else "", "poster": str(meta.get("poster") or "")[:300],
        "rating": rating, "popularity": popularity,
        "genres": _names(meta.get("genres") or meta.get("genre"))[:6],
        "director": _names(meta.get("director"), 3), "writer": _names(meta.get("writer"), 3),
        "cast": _names(meta.get("cast"), CAST_KEPT),
        "country": _names(meta.get("country"), 2),
        "words": description_words(meta.get("description") or ""),
    }


def _fold(text):
    return unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()


def description_words(text):
    """The telling words of a summary (no small or common words), each once."""
    words = []
    for word in re.findall(r"[a-z]{4,}", _fold(text)):
        if word in STOPWORDS:
            continue
        stem = word[:-1] if word.endswith("s") and not word.endswith("ss") else word
        if stem not in words:
            words.append(stem)
        if len(words) >= DESCRIPTION_WORDS:
            break
    return words


def build_pool(fetch_json, pages=None, workers=4, pause=0.05):
    """The pool, read page by page (a page that fails is skipped)."""
    from concurrent.futures import ThreadPoolExecutor
    found = {}
    lock = threading.Lock()

    def read(page):
        kind, catalog, genre, skip = page
        try:
            metas = (fetch_json(page_url(kind, catalog, genre, skip)) or {}).get("metas") or []
        except Exception:
            metas = []
        time.sleep(pause)
        with lock:
            for meta in metas:
                entry = compact(meta, kind)
                if entry and entry["id"] not in found:
                    found[entry["id"]] = entry

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(read, pages or pool_pages()))
    return list(found.values())


# --- 2. Traits and vectors ---------------------------------------------------
def traits(entry):
    """Every trait of a title, as « group:value »."""
    out = [f"g:{g.lower()}" for g in entry.get("genres") or []]
    out += [f"d:{_fold(n)}" for n in entry.get("director") or []]
    out += [f"w:{_fold(n)}" for n in entry.get("writer") or []]
    out += [f"c:{_fold(n)}" for n in (entry.get("cast") or [])[:CAST_KEPT]]
    out += [f"co:{_fold(n)}" for n in entry.get("country") or []]
    out += [f"k:{w}" for w in entry.get("words") or []]
    if str(entry.get("year") or "").isdigit():
        out.append(f"dec:{int(entry['year']) // 10 * 10}")
    return list(dict.fromkeys(out))


class Space:
    """The pool's traits with their weight: a trait shared by few titles weighs more."""

    def __init__(self, entries):
        self.entries = {e["id"]: e for e in entries}
        counts = {}
        for entry in entries:
            for trait in traits(entry):
                counts[trait] = counts.get(trait, 0) + 1
        total = max(1, len(entries))
        self.idf = {t: math.log((total + 1) / (n + 1)) + 1.0 for t, n in counts.items()}
        self.default_idf = math.log(total + 1) + 1.0
        self._vectors = {}

    def vector(self, entry):
        key = entry.get("id")
        if key in self._vectors and key in self.entries:
            return self._vectors[key]
        vec = {}
        for trait in traits(entry):
            group = trait.split(":", 1)[0]
            vec[trait] = GROUP_WEIGHT.get(group, 1.0) * self.idf.get(trait, self.default_idf)
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vec = {t: v / norm for t, v in vec.items()}
        if key in self.entries:
            self._vectors[key] = vec
        return vec


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


def quality(entry):
    rating = float(entry.get("rating") or 0)
    return max(-1.0, min(1.0, (rating - 6.5) / 2.5)) if rating else 0.0


# --- 3. The user's taste -------------------------------------------------------
# A rating out of 5 → its weight. « Aimé » (4) says more than a title only seen
# (0.6); a 3 says little; « Pas aimé » (1) counts as much as « Pas pour moi ».
RATING_WEIGHTS = ((1.0, -1.0), (2.0, -0.5), (3.0, 0.2), (4.0, 0.8), (5.0, 1.0))


def rating_weight(rating):
    value = max(1.0, min(5.0, float(rating)))
    for (low, low_weight), (high, high_weight) in zip(RATING_WEIGHTS, RATING_WEIGHTS[1:]):
        if value <= high:
            return round(low_weight + (high_weight - low_weight) * (value - low) / (high - low), 4)
    return RATING_WEIGHTS[-1][1]


def title_weight(history_entry=None, rating=None, in_list=False, not_for_me=False, now=0):
    """How much a title says about the user's taste: +1 loved … −1 disliked."""
    if not_for_me:
        return -1.0
    if rating:
        return rating_weight(rating)
    if history_entry:
        pct = float(history_entry.get("progress_pct") or 0)
        done = history_entry.get("completed") or history_entry.get("imported_watched") or pct >= 85
        if done:
            return 0.6
        last = float(history_entry.get("last_watched") or 0)
        if pct < 25 and last and now - last > 21 * 86400:
            return -0.3  # started, then left a long time ago
        return 0.2
    return 0.3 if in_list else 0.0


def taste_signals(history, watchlist, not_for_me, now):
    """{id: weight} of every title the user told something about."""
    signals = {}
    listed = {str(item.get("id") or "") for item in watchlist or []}
    for entry in history or []:
        item_id = str(entry.get("id") or "")
        if re.fullmatch(r"tt\d{1,12}", item_id):
            rating = entry.get("user_rating")
            try:
                rating = float(rating) if rating not in (None, "") else None
            except (TypeError, ValueError):
                rating = None
            signals[item_id] = title_weight(entry, rating, now=now)
    for item in watchlist or []:
        item_id = str(item.get("id") or "")
        if re.fullmatch(r"tt\d{1,12}", item_id) and item_id not in signals:
            rating = item.get("user_rating")
            try:
                rating = float(rating) if rating not in (None, "") else None
            except (TypeError, ValueError):
                rating = None
            signals[item_id] = title_weight(rating=rating, in_list=True) if rating else 0.3
    for item_id in not_for_me or ():
        signals[str(item_id)] = -1.0
    return {k: v for k, v in signals.items() if v}


def profile(space, signals, known):
    """(liked vector, disliked vector) from the titles the user told something about."""
    liked, disliked = {}, {}
    for item_id, weight in signals.items():
        entry = known.get(item_id)
        if not entry:
            continue
        target = liked if weight > 0 else disliked
        for trait, value in space.vector(entry).items():
            target[trait] = target.get(trait, 0.0) + abs(weight) * value
    for vec in (liked, disliked):
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        for trait in vec:
            vec[trait] /= norm
    return liked, disliked


# --- 4. Suggestions ----------------------------------------------------------
def _shared(a, b, group):
    names = {"d": "director", "w": "writer", "c": "cast"}[group]
    other = {_fold(n) for n in b.get(names) or []}
    return [n for n in a.get(names) or [] if _fold(n) in other]


def why_close(candidate, source):
    """The strongest reason two titles go together, in French."""
    directors = _shared(candidate, source, "d")
    if directors:
        return f"Même réalisation ({directors[0]})"
    writers = _shared(candidate, source, "w")
    if writers:
        return f"Même scénario ({writers[0]})"
    cast = _shared(candidate, source, "c")
    if cast:
        return f"Avec {cast[0]}" + (f" et {cast[1]}" if len(cast) > 1 else "")
    genres = [g for g in candidate.get("genres") or [] if g in (source.get("genres") or [])]
    year, other = str(candidate.get("year") or ""), str(source.get("year") or "")
    if genres:
        label = GENRE_FR.get(genres[0], genres[0].lower())
        if year.isdigit() and other.isdigit() and int(year) // 10 == int(other) // 10:
            return f"{label.capitalize()} des années {int(year) // 10 * 10 % 100:02d}" if int(year) < 2000 else f"{label.capitalize()} des années {int(year) // 10 * 10}"
        return f"{label.capitalize()} aussi"
    return "Thèmes proches"


def _card(entry, why=""):
    card = {"id": entry["id"], "name": entry["name"], "type": entry["type"], "year": entry.get("year") or "",
            "poster": entry.get("poster") or f"https://images.metahub.space/poster/medium/{entry['id']}/img",
            "imdbRating": f"{entry['rating']:.1f}" if entry.get("rating") else ""}
    if why:
        card["why"] = why
    return card


def _people(entry):
    return [f"d:{_fold(n)}" for n in (entry.get("director") or [])[:1]] + [f"c:{_fold(n)}" for n in (entry.get("cast") or [])[:2]]


def _pick(space, scored, limit, per_person=PER_PERSON):
    """Best first, each next one also different from those already chosen (MMR),
    and no director or lead actor in more than `per_person` of them."""
    chosen = []
    remaining = sorted(scored, key=lambda item: -item[0])[: limit * 8]
    counts = {}
    while remaining and len(chosen) < limit:
        remaining = [item for item in remaining if all(counts.get(p, 0) < per_person for p in _people(item[1]))]
        if not remaining:
            break
        best, best_value = None, None
        for index, (score, entry) in enumerate(remaining):
            closest = max((cosine(space.vector(entry), space.vector(other)) for _, other in chosen), default=0.0)
            value = (1 - DIVERSITY) * score - DIVERSITY * closest
            if best_value is None or value > best_value:
                best, best_value = index, value
        picked = remaining.pop(best)
        for person in _people(picked[1]):
            counts[person] = counts.get(person, 0) + 1
        chosen.append(picked)
    return chosen


def related(space, source, kind, exclude=(), limit=12):
    """Titles close to one title: the same people first, then genres, era and themes."""
    if not source:
        return []
    vec = space.vector(source)
    skip = set(exclude) | {source.get("id")}
    scored = []
    for entry in space.entries.values():
        if entry["type"] != kind or entry["id"] in skip:
            continue
        closeness = cosine(vec, space.vector(entry))
        if closeness > 0.05:
            scored.append((closeness + QUALITY_WEIGHT * quality(entry), entry))
    return [_card(entry, why_close(entry, source)) for _, entry in _pick(space, scored, limit)]


def for_you(space, signals, known, kind, exclude=(), limit=20):
    """Titles for this user: close to what they liked, far from what they did not."""
    liked, disliked = profile(space, signals, known)
    if not liked:
        return []
    fans = [known[i] for i, w in sorted(signals.items(), key=lambda kv: -kv[1]) if w > 0 and i in known]
    skip = set(exclude) | set(signals)
    scored = []
    for entry in space.entries.values():
        if entry["type"] != kind or entry["id"] in skip:
            continue
        vec = space.vector(entry)
        score = cosine(liked, vec) - 0.6 * cosine(disliked, vec) + QUALITY_WEIGHT * quality(entry)
        if score > 0.05:
            scored.append((score, entry))
    cards = []
    for _, entry in _pick(space, scored, limit * 2):
        vec = space.vector(entry)
        reasons = sorted(((cosine(vec, space.vector(f)), f) for f in fans[:60] if f["type"] == kind), key=lambda r: -r[0])[:2]
        reasons = [r for r in reasons if r[0] > 0.08]
        if not reasons:
            continue  # no title of the user behind it: a guess, not a suggestion
        names = " et ".join(f["name"] for _, f in reasons)
        cards.append(_card(entry, f"Parce que vous avez aimé {names} · {why_close(entry, reasons[0][1])}"))
        if len(cards) >= limit:
            break
    return cards


# --- Storage (SQLite, through kino_db's connection) ---------------------------
POOL_TABLE = """CREATE TABLE IF NOT EXISTS taste_pool (
    id TEXT PRIMARY KEY, type TEXT, data_json TEXT, updated_at REAL)"""
FEEDBACK_TABLE = """CREATE TABLE IF NOT EXISTS taste_feedback (
    media_id TEXT PRIMARY KEY, decision TEXT, at REAL)"""


class Store:
    def __init__(self, connect, lock):
        self._connect = connect
        self._lock = lock
        self._cache = None  # (updated_at, Space, entries by id)

    def _conn(self):
        conn = self._connect()
        conn.execute(POOL_TABLE)
        conn.execute(FEEDBACK_TABLE)
        return conn

    def pool_age(self, now):
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS at FROM taste_pool").fetchone()
            finally:
                conn.close()
        return (row["n"] or 0), (now - row["at"]) if row["at"] else None

    def save_pool(self, entries, now):
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.execute("DELETE FROM taste_pool")
                    conn.executemany("INSERT INTO taste_pool (id, type, data_json, updated_at) VALUES (?, ?, ?, ?)",
                                     [(e["id"], e["type"], json.dumps(e, ensure_ascii=False), now) for e in entries])
            finally:
                conn.close()
        self._cache = None

    def add_known(self, entries, now):
        """Titles fetched one by one (watched titles missing from the pool)."""
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.executemany("INSERT OR REPLACE INTO taste_pool (id, type, data_json, updated_at) VALUES (?, ?, ?, ?)",
                                     [(e["id"], e["type"], json.dumps(e, ensure_ascii=False), now) for e in entries])
            finally:
                conn.close()
        self._cache = None

    def space(self):
        if self._cache is not None:
            return self._cache
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT data_json FROM taste_pool").fetchall()
            finally:
                conn.close()
        entries = []
        for row in rows:
            try:
                entries.append(json.loads(row["data_json"]))
            except ValueError:
                continue
        self._cache = Space(entries)
        return self._cache

    def not_for_me(self):
        with self._lock:
            conn = self._conn()
            try:
                return [r["media_id"] for r in conn.execute("SELECT media_id FROM taste_feedback WHERE decision = 'not_for_me'")]
            finally:
                conn.close()

    def set_not_for_me(self, media_id, now):
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.execute("INSERT OR REPLACE INTO taste_feedback (media_id, decision, at) VALUES (?, 'not_for_me', ?)", (media_id, now))
            finally:
                conn.close()

    def reset_feedback(self):
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.execute("DELETE FROM taste_feedback")
            finally:
                conn.close()
