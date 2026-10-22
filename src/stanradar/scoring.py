"""Cheap, deterministic rule scoring.

This runs on every listing before any network or token spend. It answers two questions:
  1. Is this a hard no? (`passed` = False, with the reason recorded)
  2. How promising is it? (0-100, used to decide whether the LLM pass is worth it)

The LLM pass in `llm.py` then judges the things rules cannot: photo quality, layout,
renovation state, and whether the description is hiding something.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .commute import CommuteTable, resolve_anchor
from .config import Criteria
from .models import Listing
from .text import contains_any, fold

# Ceiling on the combined keyword bonus; see the comment at its use site.
BONUS_CAP = 12


@dataclass
class RuleResult:
    passed: bool
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    rejected_for: str | None = None


def _district_tier(listing: Listing, criteria: Criteria) -> tuple[str | None, str | None]:
    """Classify the location, returning the tier and the pattern that matched it.

    Returning the matched pattern matters for the alert: "preferred area: vracar" explains
    the score, where naming the listing's own sub-district ("Kalenic") does not.
    """
    text = fold(listing.location_text)
    if not text:
        return None, None
    d = criteria.districts
    for tier, patterns in (
        ("excluded", d.excluded),
        ("preferred", d.preferred),
        ("acceptable", d.acceptable),
    ):
        matched = contains_any(text, patterns)
        if matched:
            return tier, matched[0]
    return None, None


def _commute(listing: Listing, criteria: Criteria) -> tuple[int | None, str | None]:
    """Estimated commute minutes for this listing, or (None, None) if unknown."""
    cfg = criteria.commute
    if cfg.max_minutes is None:
        return None, None
    # Resolving the anchor builds a merged dict, so cache it: evaluate() runs on every
    # listing of every cycle, forever.
    cache_key = (cfg.anchor, tuple(sorted(cfg.areas.items())))
    table = _ANCHOR_CACHE.get(cache_key)
    if table is None:
        table = resolve_anchor(cfg.anchor, cfg.areas)
        _ANCHOR_CACHE[cache_key] = table
    return table.estimate(listing.location_text)


_ANCHOR_CACHE: dict[tuple[str, tuple[tuple[str, int], ...]], CommuteTable] = {}


def evaluate(listing: Listing, criteria: Criteria, full_detail: bool = True) -> RuleResult:
    """Score one listing against the criteria.

    `full_detail` says whether this listing already carries its description and full photo
    set. On a listing-page pass for a source that fetches details separately it is False,
    and the rules that need that data (photo count, required keywords) are deferred rather
    than failed - otherwise every 4zida listing would be rejected for having one thumbnail
    before we ever look at its nine photos.
    """
    hay = listing.searchable

    # --- hard filters: any failure stops the pipeline ---------------------
    if not criteria.price_eur.contains(listing.price_eur):
        return RuleResult(False, rejected_for=f"price {listing.price_eur}")
    if not criteria.area_m2.contains(listing.area_m2):
        return RuleResult(False, rejected_for=f"area {listing.area_m2}")
    if not criteria.rooms.contains(listing.rooms):
        return RuleResult(False, rejected_for=f"rooms {listing.rooms}")
    if listing.floor is not None and not criteria.floor.contains(float(listing.floor)):
        return RuleResult(False, rejected_for=f"floor {listing.floor}")

    ppm = listing.price_per_m2
    if criteria.max_price_per_m2 and ppm and ppm > criteria.max_price_per_m2:
        return RuleResult(False, rejected_for=f"{ppm} EUR/m2")

    if full_detail and len(listing.images) < criteria.min_images:
        return RuleResult(False, rejected_for=f"only {len(listing.images)} photos")

    tier, matched_area = _district_tier(listing, criteria)
    if tier == "excluded":
        return RuleResult(False, rejected_for=f"excluded area ({listing.location_text})")

    commute_minutes, commute_area = _commute(listing, criteria)
    if criteria.commute.max_minutes is not None:
        if commute_minutes is None:
            if criteria.commute.unknown == "exclude":
                return RuleResult(
                    False, rejected_for=f"unknown area ({listing.location_text})"
                )
        elif commute_minutes > criteria.commute.max_minutes:
            return RuleResult(
                False, rejected_for=f"~{commute_minutes} min commute ({commute_area})"
            )

    blocked = contains_any(hay, criteria.keywords.blocked)
    if blocked:
        return RuleResult(False, rejected_for=f"blocked keyword: {', '.join(blocked)}")

    if (
        full_detail
        and criteria.keywords.required_any
        and not contains_any(hay, criteria.keywords.required_any)
    ):
        return RuleResult(False, rejected_for="no required keyword")

    if criteria.owners_only is not None and listing.is_agency is not None:
        if criteria.owners_only and listing.is_agency:
            return RuleResult(False, rejected_for="agency listing")
        if not criteria.owners_only and not listing.is_agency:
            return RuleResult(False, rejected_for="owner listing")

    age = listing.age_minutes()
    if criteria.max_age_minutes and age is not None and age > criteria.max_age_minutes:
        return RuleResult(False, rejected_for=f"posted {int(age)} min ago")

    # --- soft score -------------------------------------------------------
    # Weights are set so a listing that is excellent on every axis lands near 95 rather
    # than pinning at 100. Saturating the top of the range would make every good listing
    # look identical and defeat the point of `alert_threshold`.
    score = 40
    reasons: list[str] = []

    # Cheaper within the accepted band is better; the band's full width is worth 15 points.
    lo, hi = criteria.price_eur.min, criteria.price_eur.max
    if listing.price_eur and lo is not None and hi is not None and hi > lo:
        position = (listing.price_eur - lo) / (hi - lo)
        bump = round((1 - position) * 15)
        score += bump
        if bump >= 10:
            reasons.append(f"well under budget ({listing.price_eur} EUR)")

    if criteria.max_price_per_m2 and ppm:
        margin = (criteria.max_price_per_m2 - ppm) / criteria.max_price_per_m2
        score += round(max(0.0, min(margin, 1.0)) * 8)
        if margin > 0.25:
            reasons.append(f"good value at {ppm:g} EUR/m2")

    if tier == "preferred":
        score += 12
        reasons.append(f"preferred area: {matched_area}")
    elif tier == "acceptable":
        score += 4

    # A shorter commute is the whole point of the location, so it carries real weight:
    # at the limit it adds nothing, at half the limit it adds most of the 12 points.
    limit = criteria.commute.max_minutes
    if limit and commute_minutes is not None:
        closeness = max(0.0, min(1.0, (limit - commute_minutes) / limit))
        score += round(closeness * 12)
        reasons.append(f"~{commute_minutes} min to work ({commute_area})")

    # Extra square meters over the floor, worth up to 8 points at +50% of the minimum.
    if listing.area_m2 and criteria.area_m2.min:
        extra = (listing.area_m2 - criteria.area_m2.min) / criteria.area_m2.min
        score += round(max(0.0, min(extra / 0.5, 1.0)) * 8)

    # Keyword bonuses are capped as a group. Without a cap, an agency that pads its
    # description with every amenity word outranks a better flat described plainly.
    bonus = 0
    for word, weight in criteria.keywords.bonus.items():
        if contains_any(hay, [word]):
            bonus += weight
            reasons.append(word)
    score += min(bonus, BONUS_CAP)

    for word, weight in criteria.keywords.penalty.items():
        if contains_any(hay, [word]):
            score -= weight

    if full_detail:
        if len(listing.images) >= 5:
            score += 4
        elif len(listing.images) <= 1:
            score -= 10

    if listing.is_agency is False:
        score += 4
        reasons.append("direct from owner")

    return RuleResult(True, max(0, min(100, score)), reasons)
