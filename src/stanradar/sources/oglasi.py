"""oglasi.rs - long-running classifieds portal with the best timestamps of any source.

Its listing page is clean schema.org microdata, already sorted newest-first, and every item
carries a full ISO timestamp down to the second (`<time datetime="2026-10-22T13:18:21+02:00">`).
That makes it the best source for measuring how fast the whole pipeline actually is.

Note on robots.txt: `/oglasi?q=*` and `/oglasi?o=*` (the query-string search URLs) are
disallowed, so this adapter only ever requests the canonical category path, which is not.

Verified markup (2026-10), one item per `article[itemprop=itemListElement]`:
  url / id    a.fpogl-list-title[href]        "/oglas/03-3991106/jednoiposoban-stan-..."
  title       h2[itemprop=name]
  photo       img[itemprop=image][src]
  posted      time[datetime]                  full ISO-8601 with offset
  price       span.text-price strong          "750,00 EUR"
  details     div.col-sm-6 label/value pairs  Sobnost / Kvadratura / Opremljenost / Grejanje
  geography   a[itemprop=category]            breadcrumb, coarse -> fine
  advertiser  a trailing "Reg. br." marks a registered agency
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from ..models import Listing
from ..text import detect_furnished, first_number, parse_price_eur, parse_rooms
from .base import Source

log = logging.getLogger(__name__)

BASE = "https://www.oglasi.rs"
LIST_URL = f"{BASE}/nekretnine/izdavanje-stanova/beograd"
_ID = re.compile(r"/oglas/([\w-]+)/")


def _text(node: Node | None) -> str:
    return node.text(strip=True) if node is not None else ""


class Oglasi(Source):
    name = "oglasi"
    label = "oglasi.rs"

    async def fetch_new(self, limit: int = 40) -> list[Listing]:
        response = await self.http.get(LIST_URL)
        response.raise_for_status()
        tree = HTMLParser(response.text)

        listings = []
        for article in tree.css("article[itemprop='itemListElement']"):
            try:
                listing = self._to_listing(article)
            except Exception as exc:
                log.debug("oglasi: skipped an item (%s)", exc)
                continue
            if listing:
                listings.append(listing)
            if len(listings) >= limit:
                break
        return listings

    def _to_listing(self, article: Node) -> Listing | None:
        link = article.css_first("a.fpogl-list-title")
        href = (link.attributes.get("href") if link else "") or ""
        match = _ID.search(href)
        if not match:
            return None

        # Label/value pairs: "Kvadratura: 33m2", "Sobnost: Jednoiposoban".
        details: dict[str, str] = {}
        for cell in article.css("div.col-sm-6"):
            text = _text(cell)
            if ":" in text:
                label, _, value = text.partition(":")
                details[label.strip().lower()] = value.strip()

        # Breadcrumb: Nekretnine / Izdavanje stanova / Beograd / Stari Grad / Studentski trg
        crumbs = [_text(a) for a in article.css("a[itemprop='category']")]
        geo = [c for c in crumbs if c.lower() not in {"nekretnine", "izdavanje stanova"}]

        time_node = article.css_first("time[datetime]")
        image_node = article.css_first("img[itemprop='image']")

        advertiser = _advertiser(article)
        title = _text(article.css_first("h2[itemprop='name']")) or _text(link)
        description = _text(article.css_first("p[itemprop='description']"))
        return Listing(
            source=self.name,
            external_id=match.group(1),
            url=BASE + href,
            title=title,
            description=description,
            price_eur=parse_price_eur(_text(article.css_first("span.text-price"))),
            area_m2=first_number(details.get("kvadratura")),
            rooms=parse_rooms(details.get("sobnost")),
            city=geo[0] if geo else None,
            municipality=geo[1] if len(geo) > 1 else None,
            district=geo[-1] if len(geo) > 2 else None,
            images=[image_node.attributes.get("src")]
            if image_node and image_node.attributes.get("src")
            else [],
            posted_at=_parse_iso(time_node.attributes.get("datetime") if time_node else None),
            advertiser=advertiser,
            # "Reg. br." is the agency registration number every licensed agency must show.
            is_agency=("reg. br" in advertiser.lower()) if advertiser else None,
            # "Opremljenost" is a structured field here, so prefer it over the free text.
            furnished=detect_furnished(details.get("opremljenost"))
            if details.get("opremljenost")
            else detect_furnished(title, description),
            raw={
                "furnished_raw": details.get("opremljenost"),
                "heating": details.get("grejanje"),
            },
        )


def _advertiser(article: Node) -> str | None:
    for small in article.css("small"):
        text = _text(small)
        if text and "objavljen" not in text.lower() and not text[0].isdigit():
            return text
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
