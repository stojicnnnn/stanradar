"""Commute-time filtering.

"Novi Beograd, or anywhere I can reach in a 20-minute bus ride" is not a district list -
it is a travel-time radius, and it deliberately includes places across the Sava that a
district list would miss.

Doing this *properly* needs a transit routing engine (OpenTripPlanner with Belgrade GTFS,
or a paid Directions API) plus coordinates for every listing - and only nekretnine.rs
gives coordinates at all. So this module uses a lookup table instead: an estimated
door-to-door public-transport time from each Belgrade area to the configured anchor.

The numbers below are ESTIMATES, not measured routes. They are deliberately generous at
the boundary, because at a tight budget the cost of wrongly excluding a flat is far higher
than the cost of one alert you dismiss after checking the map. Treat them as a first draft
and correct them in `config.yaml` under `criteria.commute.areas` as you learn the city -
that override is merged over these defaults, so you never have to touch this file.

The table has two tiers, and the distinction matters:

  AREAS     - specific neighbourhoods and blocks.
  FALLBACKS - municipality-wide averages, used *only* when no specific area matches.

Without that split, a municipality catch-all silently overrides its own sub-areas: Ledine
is 24 minutes away but sits inside Novi Beograd, so a single flat table would report the
municipality's 18 and make the far west of the city look closer than it is.
"""

from __future__ import annotations

from .text import fold

# Anchor: Navigator Business Center, Bulevar Milutina Milankovica 1Z, Blok 43, Novi Beograd.
# The "Milutina Milankovica" stop is ~55 m away and is served by lines 7A, 9A, 18N, 60,
# 60L, 67, 70, 95, 612, E6 and EKO1, which is what makes the Novi Beograd blocks, Zemun
# and the Cukarica side reachable without a change.
NAVIGATOR = "Navigator Business Center, Blok 43, Novi Beograd"

# Specific area (folded) -> estimated door-to-door minutes by public transport.
# Matched as substrings against the listing's location text, so "blok 45" matches
# "Blok 45, Novi Beograd, Beograd".
NAVIGATOR_AREAS: dict[str, int] = {
    # --- walking distance of the office ---------------------------------
    "blok 43": 5,
    "blok 42": 10,
    "belville": 8,
    "arena": 10,
    "blok 44": 10,
    "blok 45": 12,
    "savada": 12,
    "sava centar": 12,
    "blok 70": 14,
    "blok 64": 14,
    "novi merkator": 14,
    "mercator": 14,
    "blok 61": 15,
    "blok 62": 15,
    "dr ivana ribara": 15,
    "blok 63": 16,
    "usce": 15,
    "stari merkator": 16,
    # --- central and northern Novi Beograd ------------------------------
    "blok 20": 15,
    "blok 21": 14,
    "blok 22": 14,
    "blok 23": 15,
    "blok 24": 15,
    "blok 25": 16,
    "blok 26": 16,
    "blok 27": 17,
    "blok 28": 15,
    "blok 29": 16,
    "blok 30": 17,
    "blok 19": 16,
    "blok 1": 18,
    "blok 2": 18,
    "blok 3": 18,
    "blok 4": 18,
    "blok 5": 19,
    "blok 6": 19,
    "blok 7": 20,
    "blok 8": 20,
    "fontana": 18,
    "studentski grad": 19,
    "jurija gagarina": 16,
    "milutina milankovica": 6,
    "hotel jugoslavija": 22,
    # --- western Novi Beograd: the cheap end, still in reach ------------
    "bezanijska kosa": 22,
    "bezanija": 22,
    "paviljoni": 22,
    "ledine": 24,
    "altina": 26,
    # --- Zemun, over the Sava to the north ------------------------------
    "sava kovacevic": 24,
    "zemunske kapije": 24,
    "novi zemun": 24,
    "retenzija": 26,
    "gornji grad": 27,
    "galenika": 28,
    "plavi horizonti": 28,
    "batajnica": 45,
    # --- Cukarica, over the Sava to the south: the best value nearby ----
    "ada ciganlija": 15,
    "senjak": 16,
    "banovo brdo": 18,
    "kosutnjak": 22,
    "zarkovo": 22,
    "cerak": 26,
    "petlovo brdo": 28,
    "sremcica": 45,
    "julino brdo": 22,
    "filmski grad": 24,
    "vidikovac": 25,
    "banjica": 30,
    # --- the old city, over the Sava to the east ------------------------
    "beograd na vodi": 14,
    "brankov most": 14,
    "savamala": 15,
    "zeleni venac": 17,
    "terazije": 19,
    "beograd centar": 20,
    "slavija": 22,
    "skadarlija": 23,
    "dorcol": 24,
    "hram svetog save": 24,
    "kalenic": 26,
    "crveni krst": 28,
    "neimar": 28,
    "autokomanda": 25,
    "medakovic": 30,
    "dusanovac": 32,
    "lekino brdo": 28,
    "severni bulevar": 30,
    "bulbulder": 30,
    "karaburma": 35,
    "mirijevo": 40,
    "visnjicka banja": 42,
    "lestane": 50,
}

# Municipality-wide averages. Only consulted when nothing in AREAS matches, so these can
# stay coarse without dragging their own sub-areas towards the mean.
NAVIGATOR_FALLBACKS: dict[str, int] = {
    "novi beograd": 18,
    "cukarica": 22,
    "savski venac": 18,
    "zemun": 26,
    "stari grad": 22,
    "vracar": 26,
    "rakovica": 32,
    "vozdovac": 30,
    "zvezdara": 32,
    "palilula": 32,
    "grocka": 50,
    "surcin": 40,
    "obrenovac": 70,
    "lazarevac": 80,
    "barajevo": 60,
    "mladenovac": 80,
    "sopot": 70,
}

# Named anchors available in config. Add a new anchor by adding an entry here.
ANCHORS: dict[str, tuple[str, dict[str, int], dict[str, int]]] = {
    "navigator": (NAVIGATOR, NAVIGATOR_AREAS, NAVIGATOR_FALLBACKS),
}


class CommuteTable:
    """Two-tier area lookup for one anchor."""

    def __init__(self, description: str, areas: dict[str, int], fallbacks: dict[str, int]):
        self.description = description
        self.areas = areas
        self.fallbacks = fallbacks

    def estimate(self, location_text: str) -> tuple[int | None, str | None]:
        """Estimated minutes for a location, preferring a specific area over a municipality.

        Returns (None, None) when nothing matches - a real outcome, not an error, since
        portals invent neighbourhood names constantly. The caller decides whether an
        unrecognised area is included or dropped.
        """
        haystack = fold(location_text)
        if not haystack:
            return None, None
        for table in (self.areas, self.fallbacks):
            matches = [(area, minutes) for area, minutes in table.items() if area in haystack]
            if matches:
                # Longest pattern first, so "blok 19" beats the "blok 1" substring;
                # ties go to the lower estimate.
                area, minutes = min(matches, key=lambda kv: (-len(kv[0]), kv[1]))
                return minutes, area
        return None, None


def resolve_anchor(name: str, overrides: dict[str, int] | None = None) -> CommuteTable:
    """Build the lookup for a named anchor, with config overrides merged on top.

    Overrides land in the specific-area tier, so correcting one neighbourhood in
    `config.yaml` always takes precedence over the municipality fallback.
    """
    if name not in ANCHORS:
        raise ValueError(
            f"unknown commute anchor {name!r}; available: {', '.join(sorted(ANCHORS))}. "
            "Add one to commute.py, or set criteria.commute.areas in config.yaml."
        )
    description, areas, fallbacks = ANCHORS[name]
    merged = dict(areas)
    for area, minutes in (overrides or {}).items():
        merged[fold(area)] = minutes
    return CommuteTable(description, merged, dict(fallbacks))
