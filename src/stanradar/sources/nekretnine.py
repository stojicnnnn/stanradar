"""nekretnine.rs - one of the two biggest portals, and the richest payload of all of them.

The site is a Next.js app running on Immobiliare.it's platform (hence the Italian field
names in places), so instead of scraping its build-hashed CSS classes we read the
`__NEXT_DATA__` JSON the server embeds in the page. That gives price, surface, rooms, floor
(with lift), coordinates, the full photo list at full resolution, and whether the advertiser
is an agency or a private owner - all without a second request.

Two things to know:
  * The WAF fingerprints TLS. A plain Python request gets 403; the curl_cffi client
    gets 200. Hence `needs_browser_tls`.
  * The rental URL is /izdavanje-stanova/beograd/ (idContratto=2). The seemingly more
    specific /stambeni-objekti/stanovi/grad/beograd/izdavanje/... quietly redirects to
    the *sale* listings, which is a silent and expensive mistake to make.
  * The list payload carries no publish timestamp, so `posted_at` stays None here and
    freshness comes from id-based de-duplication plus the newest-first sort.
"""

from __future__ import annotations

import json
import logging
import re

from ..models import Listing
from ..text import detect_furnished, first_number
from .base import Source

log = logging.getLogger(__name__)

LIST_URL = "https://www.nekretnine.rs/izdavanje-stanova/beograd/"
# criterio=data orders by publication date; ordine=desc puts the newest first.
SORT = {"criterio": "data", "ordine": "desc"}
_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)


class Nekretnine(Source):
    name = "nekretnine"
    label = "nekretnine.rs"
    needs_browser_tls = True

    async def fetch_new(self, limit: int = 40) -> list[Listing]:
        response = await self.http.get(LIST_URL, params=SORT)
        response.raise_for_status()

        match = _NEXT_DATA.search(response.text)
        if not match:
            # The page shape changed, or we were served a challenge page.
            raise RuntimeError("nekretnine.rs: __NEXT_DATA__ not found in response")
        data = json.loads(match.group(1))

        queries = data["props"]["pageProps"]["dehydratedState"]["queries"]
        results = queries[0]["state"]["data"]["results"] if queries else []

        listings = []
        for result in results[:limit]:
            try:
                listing = self._to_listing(result)
            except Exception as exc:
                log.debug("nekretnine: skipped an item (%s)", exc)
                continue
            if listing:
                listings.append(listing)
        return listings

    def _to_listing(self, result: dict) -> Listing | None:
        estate = result.get("realEstate") or {}
        properties = estate.get("properties") or []
        if not properties:
            return None
        prop = properties[0]

        price = (prop.get("price") or estate.get("price") or {}).get("value")
        location = prop.get("location") or {}
        floor_info = prop.get("floor") or {}

        advertiser_info = estate.get("advertiser") or {}
        agency = advertiser_info.get("agency")
        advertiser = (agency or {}).get("displayName") if agency else None

        photos = (prop.get("multimedia") or {}).get("photos") or []
        images = [_best_photo(p) for p in photos]
        if not images and prop.get("photo"):
            images = [_best_photo(prop["photo"])]

        seo = result.get("seo") or {}
        return Listing(
            source=self.name,
            external_id=str(estate.get("id") or estate.get("uuid")),
            url=seo.get("url") or LIST_URL,
            title=estate.get("title") or seo.get("anchor") or "",
            description=prop.get("caption") or "",
            price_eur=int(price) if price else None,
            area_m2=first_number(prop.get("surface")),
            rooms=first_number(prop.get("rooms")),
            # floorOnlyValue is the bare number; abbreviation can be "PR", "SUT" etc.
            floor=_floor(floor_info),
            city=location.get("city") or location.get("region"),
            municipality=location.get("macrozone"),
            district=location.get("microzone") or location.get("macrozone"),
            address=location.get("address"),
            images=[i for i in images if i],
            posted_at=None,  # not present in the list payload
            advertiser=advertiser,
            is_agency=bool(agency),
            # The platform is Immobiliare's, so the feature list is in Italian. Presence
            # of "arredato" means furnished; absence proves nothing, since the list is
            # often incomplete - hence the fall back to the text.
            furnished=True
            if "arredato" in (prop.get("ga4features") or [])
            else detect_furnished(estate.get("title"), prop.get("caption")),
            raw={
                "elevator": prop.get("elevator"),
                "heating": prop.get("ga4Heating"),
                "garage": prop.get("ga4Garage"),
                "bathrooms": prop.get("bathrooms"),
                "features": prop.get("ga4features"),
                "lat": location.get("latitude"),
                "lon": location.get("longitude"),
            },
        )


def _floor(floor_info: dict) -> int | None:
    raw = floor_info.get("floorOnlyValue") or floor_info.get("abbreviation")
    if raw is None:
        return None
    from ..text import parse_floor

    value, _ = parse_floor(str(raw))
    return value


def _best_photo(photo: dict | None) -> str | None:
    """Prefer the largest variant; nekretnine serves xxs/s/m/l/xl per image."""
    urls = (photo or {}).get("urls") or {}
    for size in ("large", "xlarge", "medium", "small"):
        if urls.get(size):
            return urls[size]
    return next(iter(urls.values()), None)
