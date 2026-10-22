"""4zida.rs - the primary source.

4zida's own frontend talks to a public JSON API at api.4zida.rs, which is far better than
scraping: it supports `sort=createdAtDesc`, it returns an exact `createdAt` timestamp, and
one request costs a few kilobytes. A 60-second poll on this endpoint means an alert
typically lands in Discord within a minute of the listing going live.

Verified endpoints (2026-10):
  GET /v6/search/apartments?for=rent&placeIds[]=2&sort=createdAtDesc&page=1
  GET /v6/search/houses?...  -> same response shape, ~450 Belgrade rentals
  GET /v6/ads/{id}           -> full description + all photos

Only `for`, `placeIds[]`, `priceFrom`, `priceTo`, `m2From`, `m2To`, `page` and `sort` are
known-good query params; anything else returns 422. We therefore filter broadly server-side
and let `scoring.py` do the rest locally - which also means a mis-tagged listing (wrong
`structure`, missing `furnished`) still reaches us instead of being silently filtered out.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..models import Listing
from ..text import detect_furnished
from .base import Source

log = logging.getLogger(__name__)

API = "https://api.4zida.rs/v6"
BELGRADE_PLACE_ID = 2


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _best_image(image: dict[str, Any] | None) -> str | None:
    """Pick the largest JPEG variant available in one of 4zida's image dicts."""
    if not image:
        return None
    for bucket in ("adDetails", "search", "gallery"):
        variants = image.get(bucket)
        if not isinstance(variants, dict):
            continue
        jpegs = [(k, v) for k, v in variants.items() if k.endswith("_jpeg")]
        if jpegs:
            # Keys look like "730x396_fill_0_jpeg"; sort by leading width.
            jpegs.sort(key=lambda kv: int(kv[0].split("x")[0]) if kv[0][0].isdigit() else 0)
            return jpegs[-1][1]
    return None


class FourZida(Source):
    name = "fourzida"
    label = "4zida.rs"
    # The search response carries one thumbnail and a 100-character description; the real
    # photo set and full text only arrive from /v6/ads/{id}.
    has_detail_fetch = True

    # 4zida keeps houses behind a separate endpoint with an identical response shape.
    ENDPOINTS = {"apartment": "apartments", "house": "houses"}

    def __init__(
        self,
        client,
        price_to: int | None = None,
        m2_from: int | None = None,
        property_types: list[str] | None = None,
    ):
        super().__init__(client)
        # Broad server-side pre-filter only; everything precise happens locally.
        self.price_to = price_to
        self.m2_from = m2_from
        self.property_types = property_types or ["apartment"]

    async def fetch_new(self, limit: int = 40) -> list[Listing]:
        listings: list[Listing] = []
        for property_type in self.property_types:
            endpoint = self.ENDPOINTS.get(property_type)
            if not endpoint:
                continue
            params: list[tuple[str, Any]] = [
                ("for", "rent"),
                ("placeIds[]", BELGRADE_PLACE_ID),
                ("sort", "createdAtDesc"),
                ("page", 1),
            ]
            if self.price_to:
                # Headroom, so a listing priced slightly over still gets scored and logged.
                params.append(("priceTo", int(self.price_to * 1.2)))
            if self.m2_from:
                params.append(("m2From", max(1, int(self.m2_from * 0.8))))

            response = await self.http.get(f"{API}/search/{endpoint}", params=params)
            response.raise_for_status()
            ads = response.json().get("ads", [])
            listings.extend(self._to_listing(ad, property_type) for ad in ads[:limit])
        return listings

    def _to_listing(self, ad: dict[str, Any], property_type: str = "apartment") -> Listing:
        places = ad.get("placeNames") or []
        thumb = _best_image(ad.get("image"))
        return Listing(
            source=self.name,
            external_id=str(ad["id"]),
            url="https://www.4zida.rs" + (ad.get("urlPath") or ""),
            title=ad.get("detailedTitle") or ad.get("title") or "",
            description=ad.get("description100") or "",
            price_eur=int(ad["price"]) if ad.get("price") else None,
            area_m2=float(ad["m2"]) if ad.get("m2") else None,
            rooms=float(ad["roomCount"]) if ad.get("roomCount") else None,
            property_type=property_type,
            floor=ad.get("redactedFloor"),
            total_floors=ad.get("redactedTotalFloors"),
            # placeNames is fine-to-coarse: ["Zemunske kapije", "Zemun opstina", "Beograd"]
            district=places[0] if places else None,
            municipality=places[1] if len(places) > 1 else None,
            city=places[-1] if places else None,
            address=ad.get("safeAddress") or ad.get("address"),
            images=[thumb] if thumb else [],
            posted_at=_parse_ts(ad.get("createdAt")),
            advertiser=(ad.get("agencyUrl") or "").rsplit("/", 2)[-2].replace("-", " ").title()
            if ad.get("agencyUrl")
            else None,
            is_agency=bool(ad.get("agencyUrl")),
            raw={"placeId": ad.get("placeId"), "imageCount": ad.get("imageCount")},
        )

    async def enrich(self, listing: Listing) -> Listing:
        response = await self.http.get(f"{API}/ads/{listing.external_id}")
        response.raise_for_status()
        detail = response.json()

        listing.description = detail.get("desc") or listing.description
        listing.title = detail.get("headline") or listing.title
        images = [_best_image(img) for img in (detail.get("images") or [])]
        listing.images = [i for i in images if i] or listing.images

        author = detail.get("author") or {}
        agency = author.get("agency")
        if agency:
            listing.advertiser = agency.get("name") or listing.advertiser
            listing.is_agency = True
        else:
            listing.is_agency = False

        # The detail payload states this explicitly as "yes"/"no".
        furnished = detail.get("furnished")
        if furnished in ("yes", "no"):
            listing.furnished = furnished == "yes"
        else:
            listing.furnished = detect_furnished(listing.title, listing.description)
        listing.raw["heating"] = detail.get("heatingType")

        if detail.get("url"):
            listing.url = detail["url"]
        listing.raw["status"] = detail.get("status")
        listing.raw["structureName"] = detail.get("structureName")
        return listing
