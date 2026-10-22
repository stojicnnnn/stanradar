"""Discord webhook alerts.

One embed per listing, built so the decision is readable from the phone notification alone:
the title carries price and size, the thumbnail shows the flat, and the AI verdict is the
first line of the body. A Discord webhook takes at most 10 embeds per request and starts
rate-limiting (429) past roughly 5 requests per 2 seconds per webhook, so sends are
serialized with a small gap and 429s are honoured.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .config import DiscordConfig
from .llm import Verdict
from .models import Listing
from .scoring import RuleResult

log = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 3

# Green for strong fits, amber for decent ones, grey for the rest.
_COLORS = [(85, 0x2ECC71), (70, 0x3498DB), (55, 0xF1C40F), (0, 0x95A5A6)]


def _color(score: int) -> int:
    for threshold, color in _COLORS:
        if score >= threshold:
            return color
    return _COLORS[-1][1]


def _field(name: str, value: str, inline: bool = True) -> dict:
    return {"name": name, "value": value or "-", "inline": inline}


def _who(listing: Listing) -> str:
    if listing.is_agency:
        return "agency"
    return "owner" if listing.is_agency is False else "-"


def _furnishing(listing: Listing) -> str:
    """An empty flat means buying a bed, a fridge and a washing machine, so say so."""
    if listing.furnished is True:
        return "furnished"
    if listing.furnished is False:
        return "empty - needs furnishing"
    return "not stated"


class Discord:
    def __init__(self, cfg: DiscordConfig, client: httpx.AsyncClient):
        self.cfg = cfg
        self.http = client
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.cfg.webhook_url)

    async def send_listing(
        self,
        listing: Listing,
        rules: RuleResult,
        verdict: Verdict | None,
        duplicate_of: str | None = None,
    ) -> None:
        score = verdict.fit if verdict else rules.score

        price = f"{listing.price_eur} EUR" if listing.price_eur else "price n/a"
        size = f"{listing.area_m2:g} m2" if listing.area_m2 else "size n/a"
        title = f"{price} - {size}"
        if listing.rooms:
            title += f" - {listing.rooms:g} rooms"

        fields = [
            _field("Location", listing.location_text or "unknown"),
            _field(
                "EUR/m2", f"{listing.price_per_m2:g}" if listing.price_per_m2 else "-"
            ),
            _field(
                "Floor",
                f"{listing.floor}/{listing.total_floors}"
                if listing.floor is not None and listing.total_floors
                else (str(listing.floor) if listing.floor is not None else "-"),
            ),
            _field("Advertiser", listing.advertiser or _who(listing)),
            _field("Furnished", _furnishing(listing)),
            _field("Score", f"rules {rules.score}" + (f" | AI {verdict.fit}" if verdict else "")),
            _field("Source", listing.source),
        ]

        lines: list[str] = []
        if verdict:
            lines.append(f"**{verdict.verdict}**")
            lines.append(f"Condition: {verdict.condition}")
            if verdict.pros:
                lines.append("Good: " + ", ".join(verdict.pros[:3]))
            if verdict.cons:
                lines.append("Watch: " + ", ".join(verdict.cons[:3]))
            if verdict.red_flags:
                lines.append(":warning: " + "; ".join(verdict.red_flags[:3]))
        elif rules.reasons:
            lines.append("Matched: " + ", ".join(rules.reasons[:4]))

        if duplicate_of:
            lines.append(f"Possibly the same flat as <{duplicate_of}>")
        if listing.description:
            lines.append("")
            lines.append(listing.description[:400].strip())

        embed = {
            "title": title[:250],
            "url": listing.url,
            "description": "\n".join(lines)[:2000],
            "color": _color(score),
            "fields": fields,
            "footer": {"text": listing.title[:100] or listing.source},
        }
        if listing.images:
            embed["thumbnail"] = {"url": listing.images[0]}
        if listing.posted_at:
            embed["timestamp"] = listing.posted_at.isoformat()

        content = ""
        if self.cfg.mention and score >= self.cfg.mention_min_score:
            content = self.cfg.mention

        await self._post({"content": content, "embeds": [embed]})

    async def send_text(self, text: str) -> None:
        await self._post({"content": text[:1900]})

    async def _post(self, payload: dict) -> None:
        if not self.configured:
            log.warning("discord: no webhook configured, dropping message")
            return
        # Serialize sends so a burst of new listings cannot trip the webhook rate limit.
        async with self._lock:
            for _ in range(MAX_SEND_ATTEMPTS):
                try:
                    response = await self.http.post(self.cfg.webhook_url, json=payload)
                except httpx.HTTPError as exc:
                    log.warning("discord: transport error (%s)", exc)
                    return
                if response.status_code == 429:
                    retry_after = float(
                        response.json().get("retry_after", 1.0) if response.content else 1.0
                    )
                    log.info("discord: rate limited, waiting %.1fs", retry_after)
                    await asyncio.sleep(min(retry_after, 10) + 0.25)
                    continue
                if response.status_code >= 400:
                    log.warning("discord: %s %s", response.status_code, response.text[:300])
                    return
                await asyncio.sleep(0.35)
                return
            log.warning("discord: gave up after %d attempts", MAX_SEND_ATTEMPTS)
