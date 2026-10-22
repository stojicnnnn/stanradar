"""kupujemprodajem.com - the big owner-to-owner marketplace.

Worth having precisely because it is where private landlords post, so it carries flats that
never reach the agency portals. It is also the only source here with a meaningful number of
listings at the bottom of the price range.

How it is read, and why this way:

KP is a Next.js app. Its category pages (`/nekretnine-izdavanje/stanovi`) render client-side
and arrive with no listings in the HTML at all - the data comes from `/api/`, which the
site's own `robots.txt` disallows, so this adapter does not touch it. The **search** page
`/pretraga?keywords=...` is not disallowed and *does* server-render its results into
`__NEXT_DATA__` under `initialReduxState.search.lastSearchResult.ads`, with price, area,
rooms, floor, a full `posted` timestamp, the location hierarchy and an owner flag. That is
the path used here.

The ad page itself also server-renders, under `initialReduxState.ad.byId.{id}`, which is
where the full photo set and the complete description come from - the search payload
carries only one thumbnail, so `enrich()` is required here, not optional.

Three quirks:
  * Paid-promotion ads are pinned above the newest ones, so the first result is often old.
    Order therefore cannot be trusted; freshness comes from id-based de-duplication.
  * Because this is a keyword search rather than a category, the query matters. The default
    covers Belgrade rentals broadly and lets `scoring.py` do the filtering.
  * The search mixes in `Poslovni prostor` (commercial premises) and rooms, so results are
    filtered down to the `Stanovi` group.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..models import Listing
from ..text import detect_furnished, first_number, parse_floor, parse_rooms
from .base import Source

log = logging.getLogger(__name__)

BASE = "https://www.kupujemprodajem.com"
SEARCH_URL = f"{BASE}/pretraga"
IMAGE_BASE = "https://images.kupujemprodajem.com"
DEFAULT_QUERY = "izdavanje stana beograd"
BELGRADE = ZoneInfo("Europe/Belgrade")

_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S
)
# Only apartment rentals; KP's search also returns rooms, garages and sale listings.
_RENT_GROUP = "stanovi"


class KupujemProdajem(Source):
    name = "kupujemprodajem"
    label = "kupujemprodajem.com"
    # The search payload has one thumbnail and a truncated description; the real photo set
    # and full text only exist on the ad page.
    has_detail_fetch = True

    def __init__(self, client, query: str = DEFAULT_QUERY):
        super().__init__(client)
        self.query = query

    async def fetch_new(self, limit: int = 40) -> list[Listing]:
        response = await self.http.get(SEARCH_URL, params={"keywords": self.query})
        response.raise_for_status()

        match = _NEXT_DATA.search(response.text)
        if not match:
            raise RuntimeError("kupujemprodajem: __NEXT_DATA__ not found in response")
        state = json.loads(match.group(1)).get("props", {}).get("initialReduxState", {})
        result = (state.get("search") or {}).get("lastSearchResult") or {}
        ads = result.get("ads") or []

        listings = []
        for ad in ads[:limit]:
            try:
                listing = self._to_listing(ad)
            except Exception as exc:
                log.debug("kupujemprodajem: skipped an item (%s)", exc)
                continue
            if listing:
                listings.append(listing)
        return listings

    def _to_listing(self, ad: dict) -> Listing | None:
        # The keyword search is broad; keep only apartment rentals.
        if _RENT_GROUP not in (ad.get("group_name") or "").lower():
            return None
        if "izdavanje" not in (ad.get("category_name") or "").lower():
            return None
        if (ad.get("currency") or "").lower() not in ("eur", ""):
            return None

        attrs = _attributes(ad)
        floor, total_floors = parse_floor(attrs.get("realEstateFloorInfo"))

        # "Beograd | Opstina Novi Beograd | Hotel Jugoslavija", coarse -> fine.
        parts = [p.strip() for p in (ad.get("location_name") or "").split("|") if p.strip()]

        photo = ad.get("photo_path1") or ad.get("photo1_tmb_300x300")
        images = [IMAGE_BASE + photo] if photo else []

        return Listing(
            source=self.name,
            external_id=str(ad["ad_id"]),
            url=BASE + (ad.get("ad_url") or ""),
            title=ad.get("name_decoded") or ad.get("name") or "",
            description=ad.get("description_snippet_decoded")
            or ad.get("description_snippet")
            or "",
            price_eur=int(ad["price"]) if ad.get("price") else None,
            area_m2=first_number(attrs.get("realEstateArea")),
            rooms=parse_rooms(attrs.get("realEstateApartmentNumberOfRooms")),
            floor=floor,
            total_floors=total_floors,
            city=parts[0] if parts else None,
            municipality=parts[1] if len(parts) > 1 else None,
            district=parts[-1] if len(parts) > 2 else None,
            images=images,
            posted_at=_parse_posted(ad.get("posted")),
            advertiser="private owner" if ad.get("show_directly_from_owner") else None,
            # KP has no structured furnishing attribute; enrich() re-checks against the
            # full description, which is far more likely to mention it than the snippet.
            furnished=detect_furnished(
                ad.get("name_decoded"), ad.get("description_snippet_decoded")
            ),
            # KP's own badge for "directly from owner"; absent means a reseller or agency.
            is_agency=not ad["show_directly_from_owner"]
            if "show_directly_from_owner" in ad
            else None,
            raw={
                "promoted": bool(ad.get("is_display_priority") or ad.get("is_priority")),
                "views": ad.get("view_count"),
                "posted_desc": ad.get("posted_desc"),
            },
        )


def _attributes(ad: dict) -> dict[str, str]:
    """Flatten KP's nested attribute sections into {code: first value}."""
    flat: dict[str, str] = {}
    for section in ad.get("ad_attributes") or []:
        for attribute in section.get("attributes") or []:
            code = attribute.get("code")
            values = attribute.get("values") or []
            if code and values:
                flat[code] = str(values[0])
    return flat


def _parse_posted(value: str | None) -> datetime | None:
    """'2026-10-22 02:16:39' -> aware datetime.

    KP reports Belgrade wall-clock time with no offset. Using a fixed +02:00 would be an
    hour wrong for half the year, which would make `age_minutes` - and so the freshness
    shown in the alert - wrong, so attach the real zone and let it handle DST.
    """
    if not value or value.startswith("0000"):
        return None
    try:
        naive = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=BELGRADE)


    async def enrich(self, listing: Listing) -> Listing:
        """Fetch the ad page for the full photo set and the complete description."""
        response = await self.http.get(listing.url)
        response.raise_for_status()

        match = _NEXT_DATA.search(response.text)
        if not match:
            return listing
        state = json.loads(match.group(1)).get("props", {}).get("initialReduxState", {})
        ad = ((state.get("ad") or {}).get("byId") or {}).get(listing.external_id)
        if not ad:
            return listing

        photos = [p.get("original") or p.get("thumbnail") for p in (ad.get("photos") or [])]
        listing.images = [p for p in photos if p] or listing.images

        if ad.get("description"):
            listing.description = _strip_html(ad["description"])
            furnished = detect_furnished(listing.title, listing.description)
            if furnished is not None:
                listing.furnished = furnished

        user = ad.get("user") or {}
        if user.get("username"):
            listing.advertiser = user["username"]
        # KP makes sellers declare whether they are a trader; that declaration is a better
        # agency signal than the search page's owner badge.
        if user.get("trader"):
            listing.is_agency = True
        return listing


_TAGS = re.compile(r"<[^>]+>")
_ENTITIES = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'"}


def _strip_html(html: str) -> str:
    """KP descriptions are HTML fragments; the model and the Discord embed want plain text."""
    text = _TAGS.sub("\n", html)
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return re.sub(r"\n{2,}", "\n", text).strip()
