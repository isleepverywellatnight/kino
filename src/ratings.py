"""« Noter en un geste »: one rating per title, given at the end of a
playback, from the Home row or from the fiche, and kept in its history entry.

The three gestures are Letterboxd values, so an imported note and a KINO note
read the same way everywhere:

    « Pas aimé » 1/5      « Aimé » 4/5      « Adoré » 5/5

History fields:
    user_rating     '' or '0.5' … '5' by halves
    rated_at        when it was given or taken back (the newer one wins a Drive sync)
    rating_asked    'film' or 'S2': the last end of playback that asked about it
    rating_skipped  True once « Passer » took it out of the Home row
"""
import re

GESTURES = {"disliked": "1", "liked": "4", "loved": "5"}
_ASKED = re.compile(r"^(film|S\d{1,3})$")


def clean_rating(value):
    """'' (no rating) or a value from 0.5 to 5 by halves, as text ('4', '4.5')."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        raise ValueError("Note invalide.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Note invalide.") from None
    if not 0.5 <= number <= 5 or number * 2 != int(number * 2):
        raise ValueError("Note invalide.")
    return f"{number:.1f}".rstrip("0").rstrip(".")


def patch_for(body, now):
    """The history fields that a POST /api/rating body changes."""
    if not isinstance(body, dict):
        raise ValueError("Rien à enregistrer.")
    fields = {}
    if "rating" in body:
        fields.update(user_rating=clean_rating(body.get("rating")), rated_at=float(now), rating_skipped=False)
    if "asked" in body:
        asked = str(body.get("asked") or "")
        if not _ASKED.match(asked):
            raise ValueError("Moment de la question inconnu.")
        fields["rating_asked"] = asked
    if "skip" in body and "rating" not in body:
        fields["rating_skipped"] = bool(body.get("skip"))
    if not fields:
        raise ValueError("Rien à enregistrer.")
    return fields


def _stamp(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def newer_rating(kept, other):
    """`kept`, with the rating of `other` when `other` was rated later.

    A Drive sync keeps the entry played last; the rating can be newer on the
    other Mac, and must not be lost with it."""
    if not isinstance(kept, dict) or not isinstance(other, dict):
        return kept
    if _stamp(other.get("rated_at")) > _stamp(kept.get("rated_at")):
        return {**kept, "user_rating": str(other.get("user_rating") or ""), "rated_at": other.get("rated_at")}
    return kept
