"""halooglasi.com - Serbia's largest general classifieds, strong on real estate.

No public JSON API, but the listing page is server-rendered and the "newest first" sort is
reachable by its sort GUID. Its WAF fingerprints the TLS handshake - a plain Python request
gets a 403 no matter what headers are sent - so this source uses the curl_cffi client.

Verified markup (2026-10), one item per `div.product-item[data-id]`:
  price        .central-feature span[data-value]      "1.550"
  title        h3.product-title a                     text + href
  photo        figure.pi-img-wrapper img[src]
  photo count  .pi-img-count-num
  posted       .publish-date                           "22.10.2026." (day precision only)
  advertiser   span[data-field-name=oglasivac_nekretnine_s][data-field-value]
  geography    ul.subtitle-places li                   coarse -> fine
  features     ul.product-features li .value-wrapper   m2 / rooms / floor, each with
                                                       a .legend label naming the field

Because `publish-date` has no clock time, freshness here comes from polling rather than
from the timestamp: a listing is "new" when its id first appears.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from ..models import Listing
from ..text import detect_furnished, first_number, parse_floor, parse_price_eur, parse_rooms
from .base import Source

log = logging.getLogger(__name__)

BASE = "https://www.halooglasi.com"
LIST_URL = f"{BASE}/nekretnine/izdavanje-stanova/beograd"
# QuidditaEnvironment.SortingFields -> "Prvo najnoviji" (ValidFromForDisplay, descending)
SORT_NEWEST = "b0545d50-79fb-4ba5-a2d5-d1f08a6dbdb3"


def _text(node: Node | None) -> str:
    return node.text(strip=True) if node is not None else ""


class HaloOglasi(Source):
    name = "halooglasi"
    label = "halooglasi.com"
    needs_browser_tls = True

    async def fetch_new(self, limit: int = 40) -> list[Listing]:
        response = await self.http.get(LIST_URL, params={"sort": SORT_NEWEST})
        response.raise_for_status()
        tree = HTMLParser(response.text)

        listings = []
        for item in tree.css("div.product-item[data-id]"):
            # Promoted banner slots reuse the product-item class but carry no real ad.
            if "banner-list" in (item.attributes.get("class") or ""):
                continue
            try:
                listings.append(self._to_listing(item))
            except Exception as exc:  # one malformed card must not kill the cycle
                log.debug("halooglasi: skipped an item (%s)", exc)
            if len(listings) >= limit:
                break
        return listings

    def _to_listing(self, item: Node) -> Listing:
        ad_id = item.attributes["data-id"]
        link = item.css_first("h3.product-title a")
        href = (link.attributes.get("href") if link else "") or ""

        price_node = item.css_first(".central-feature span")
        price = parse_price_eur(
            (price_node.attributes.get("data-value") if price_node else None) or _text(price_node)
        )

        places = [_text(li) for li in item.css("ul.subtitle-places li")]
        places = [p for p in places if p]  # coarse -> fine: Beograd, Opstina Vracar, Crveni krst

        area = rooms = None
        floor = total_floors = None
        for feature in item.css("ul.product-features .value-wrapper"):
            legend = _text(feature.css_first(".legend")).lower()
            # The legend sits inside the value, so strip it to get the bare value.
            value = _text(feature).replace(_text(feature.css_first(".legend")), "").strip()
            if "kvadratura" in legend:
                area = first_number(value)
            elif "soba" in legend:
                rooms = parse_rooms(value)
            elif "spratnost" in legend:
                floor, total_floors = parse_floor(value)

        advertiser_node = item.css_first("span[data-field-name='oglasivac_nekretnine_s']")
        advertiser_kind = (
            advertiser_node.attributes.get("data-field-value") if advertiser_node else None
        )

        image_node = item.css_first("figure.pi-img-wrapper img")
        image = (image_node.attributes.get("src") if image_node else None) or ""
        # Thumbs/<date>/m/ is the list thumbnail; the same path without /Thumbs/.../m is
        # the full-size photo, which is what we want to hand to the vision model.
        if image and "/Thumbs/" in image:
            image = image.replace("/Thumbs/", "/").replace("/m/", "/")

        return Listing(
            source=self.name,
            external_id=ad_id,
            url=BASE + href if href.startswith("/") else href or f"{LIST_URL}/{ad_id}",
            title=_text(link),
            description=_text(item.css_first("p.product-description")),
            price_eur=price,
            area_m2=area,
            rooms=rooms,
            floor=floor,
            total_floors=total_floors,
            city=places[0] if places else None,
            municipality=places[1] if len(places) > 1 else None,
            district=places[-1] if len(places) > 2 else None,
            images=[image] if image.startswith("http") else [],
            posted_at=_parse_date(_text(item.css_first(".publish-date"))),
            advertiser=_text(advertiser_node) or None,
            # Not a structured field on this portal, so the text is all we have.
            furnished=detect_furnished(_text(link), _text(item.css_first("p.product-description"))),
            is_agency=(advertiser_kind == "agencija") if advertiser_kind else None,
            raw={"photo_count": _text(item.css_first(".pi-img-count-num"))},
        )


def _parse_date(value: str) -> datetime | None:
    """'22.10.2026.' -> midnight UTC that day. Day precision is all the page gives."""
    value = value.strip().rstrip(".")
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d.%m.%Y").replace(tzinfo=UTC)
    except ValueError:
        return None
