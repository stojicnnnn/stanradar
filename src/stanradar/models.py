"""The normalized listing every source adapter must produce."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .text import fold


@dataclass(slots=True)
class Listing:
    source: str
    external_id: str
    url: str
    title: str = ""
    description: str = ""
    price_eur: int | None = None
    area_m2: float | None = None
    rooms: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    # Belgrade geography, coarse to fine: "Beograd" / "Vracar" / "Neimar"
    city: str | None = None
    municipality: str | None = None
    district: str | None = None
    address: str | None = None
    images: list[str] = field(default_factory=list)
    posted_at: datetime | None = None
    advertiser: str | None = None
    is_agency: bool | None = None
    # True furnished, False empty, None not stated. An empty flat is cheaper but means
    # buying everything, so this belongs in the alert rather than only in the score.
    furnished: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def price_per_m2(self) -> float | None:
        if self.price_eur and self.area_m2:
            return round(self.price_eur / self.area_m2, 2)
        return None

    @property
    def location_text(self) -> str:
        parts = [self.district, self.municipality, self.city, self.address]
        return ", ".join(p for p in parts if p)

    @property
    def searchable(self) -> str:
        """Folded haystack for keyword rules."""
        return fold(f"{self.title} {self.description} {self.location_text}")

    def fingerprint(self) -> str:
        """Strong identity of the *flat*: price, size, rooms and district.

        Agencies re-post the same apartment repeatedly, and this collapses those into one
        alert. Including the district keeps it conservative enough to suppress on.
        """
        bits = [
            str(self.price_eur or ""),
            str(int(self.area_m2) if self.area_m2 else ""),
            str(self.rooms or ""),
            fold(self.district or self.municipality or ""),
        ]
        return hashlib.sha1("|".join(bits).encode()).hexdigest()[:16]

    def loose_fingerprint(self) -> str | None:
        """Identity without the district, for spotting the same flat across portals.

        Portals disagree on neighbourhood names - one flat on Uroša Trojanovića is filed
        under "Bulbulder" by 4zida and "Severni bulevar" by nekretnine.rs - so the strong
        fingerprint cannot match across sources. This one drops the district.

        Returns None unless price, area and rooms are all known, because a partial match
        here is far too weak to act on. Even a full match is only ever used to *annotate*
        an alert, never to suppress one: two genuinely different flats can share a price,
        a size and a room count, and missing a listing is the expensive mistake.
        """
        if not (self.price_eur and self.area_m2 and self.rooms):
            return None
        bits = [str(self.price_eur), str(int(self.area_m2)), str(self.rooms)]
        return hashlib.sha1("|".join(bits).encode()).hexdigest()[:16]

    def age_minutes(self) -> float | None:
        if not self.posted_at:
            return None
        now = datetime.now(UTC)
        return (now - self.posted_at).total_seconds() / 60
