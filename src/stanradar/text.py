"""Serbian text helpers: diacritic folding and number extraction.

Listings mix Latin with and without diacritics, Cyrillic, and sloppy spelling, so every
keyword comparison in the app goes through `fold()` first.
"""

from __future__ import annotations

import re
import unicodedata

# Serbian Latin digraphs and Cyrillic need explicit mapping; NFKD alone does not cover them.
_MAP = str.maketrans(
    {
        "đ": "dj", "Đ": "dj", "ђ": "dj", "Ђ": "dj",
        "ž": "z", "Ž": "z", "ж": "z", "Ж": "z",
        "ć": "c", "Ć": "c", "ћ": "c", "Ћ": "c",
        "č": "c", "Č": "c", "ч": "c", "Ч": "c",
        "š": "s", "Š": "s", "ш": "s", "Ш": "s",
        "џ": "dz", "Џ": "dz", "љ": "lj", "Љ": "lj", "њ": "nj", "Њ": "nj",
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "з": "z",
        "и": "i", "ј": "j", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h",
        "ц": "c",
    }
)


def fold(s: str | None) -> str:
    """Lowercase, strip diacritics, transliterate Cyrillic, collapse whitespace."""
    if not s:
        return ""
    s = s.lower().translate(_MAP)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def contains_any(haystack: str, needles: list[str]) -> list[str]:
    """Return the folded needles present in `haystack` (which must already be folded)."""
    return [n for n in (fold(x) for x in needles) if n and n in haystack]


_NUM = re.compile(r"(\d+(?:[.,]\d+)?)")


def first_number(s: str | None) -> float | None:
    """Parse the first number, coping with both '1.250' (thousands) and '3.0' (decimal).

    Serbian listings use '.' for thousands and ',' for decimals, but portals are
    inconsistent and some emit plain '3.0' room counts, so decide per separator.
    """
    if not s:
        return None
    m = _NUM.search(s)
    if not m:
        return None
    raw = m.group(1)
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "." in raw:
        whole, _, frac = raw.partition(".")
        # Exactly three trailing digits means a thousands separator, not a decimal point.
        raw = whole + frac if len(frac) == 3 else raw
    try:
        return float(raw)
    except ValueError:
        return None


def parse_price_eur(s: str | None) -> int | None:
    """'1.200 €', '1200 EUR', '800e' -> 1200 / 800. Returns None if no plausible price."""
    if not s:
        return None
    digits = re.sub(r"[^\d]", "", s.split(",")[0])
    if not digits:
        return None
    try:
        val = int(digits)
    except ValueError:
        return None
    # Guard against phone numbers / ids slipping into a price field.
    return val if 50 <= val <= 50_000 else None


# Serbian room-count words as used by every portal, mapped to the numeric convention
# the portals' own filters use (a "jednoiposoban" is a 1.5).
ROOM_WORDS = {
    "garsonjera": 1.0,
    "garsonjeru": 1.0,
    "jednosoban": 1.0,
    "jednoiposoban": 1.5,
    "dvosoban": 2.0,
    "dvoiposoban": 2.5,
    "trosoban": 3.0,
    "troiposoban": 3.5,
    "cetvorosoban": 4.0,
    "cetvoroiposoban": 4.5,
    "petosoban": 5.0,
    "vise": 5.0,
}


def parse_rooms(s: str | None) -> float | None:
    """'Dvoiposoban' -> 2.5, '3.0' -> 3.0, '2+1' -> 2.0."""
    if not s:
        return None
    f = fold(s)
    for word, value in ROOM_WORDS.items():
        if word in f:
            return value
    return first_number(f)


_ROMAN = {"i": 1, "v": 5, "x": 10}
# Floor shorthands. Distinguishing a suteren from a high ground floor matters: one is a
# half-basement with windows at pavement level, the other is a desirable raised floor.
FLOOR_WORDS = {
    "psut": -2,  # podrum / cellar
    "podrum": -2,
    "sut": -1,  # suteren / half-basement
    "suteren": -1,
    "npr": 0,  # nizak parter
    "pr": 0,  # parter / ground floor
    "parter": 0,
    "vpr": 0,  # visoko parter - counted as ground, scored better via keywords
}


def parse_floor(s: str | None) -> tuple[int | None, int | None]:
    """'VI/7' -> (6, 7); 'SUT/5' -> (-1, 5); '3/5' -> (3, 5); 'PR' -> (0, None)."""
    if not s:
        return None, None
    f = fold(s).replace(" ", "")
    head, _, tail = f.partition("/")
    total = int(first_number(tail)) if tail and first_number(tail) else None

    if head in FLOOR_WORDS:
        return FLOOR_WORDS[head], total
    if head.isdigit():
        return int(head), total
    if head and all(c in _ROMAN for c in head):
        return _roman(head), total
    return None, total


def _roman(s: str) -> int:
    total, prev = 0, 0
    for c in reversed(s):
        v = _ROMAN[c]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total
