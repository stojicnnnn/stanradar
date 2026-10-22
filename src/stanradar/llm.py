"""Vision + text fit scoring with Claude.

Rules in `scoring.py` can check price, size and district. They cannot tell a tastefully
renovated flat from one with a 1987 kitchen and a mattress on the floor, and that is
usually what decides whether a listing is worth a phone call. This module sends the
photos and the full Serbian description to Claude and gets back a structured verdict.

Only listings that already cleared the rules are sent here (see `LlmConfig.min_rule_score`),
so the token spend tracks the number of plausible listings, not the number of listings.
"""

from __future__ import annotations

import logging

import anthropic
from pydantic import BaseModel, Field

from .config import LlmConfig
from .models import Listing

log = logging.getLogger(__name__)

SYSTEM = """You help someone find a long-term rental apartment in Belgrade, Serbia.

You will get one listing: its numbers, its Serbian-language description, and its photos.
Judge how well it fits the stated preferences, with weight on what only the photos and the
free text can tell you:

- Real condition: renovated vs. tired vs. actively run down. Judge the kitchen and bathroom
  hardest, as those are the expensive things a tenant cannot fix.
- Natural light, ceiling height, window size, and what the windows look out on.
- Layout sanity: pass-through rooms, a kitchen in a corridor, a bedroom with no window.
- Photo honesty: very few photos, no bathroom or kitchen shot, heavy wide-angle distortion,
  stock or render images, or photos of a different unit are all warning signs.
- Red flags in the text: "idealno za studente" packed with beds, short-term or daily rental
  disguised as long-term, agency fee demands, suteren or basement sold as a ground floor,
  no heating, or a deposit far above the normal one month.

Be a skeptical reader. Serbian listings routinely oversell. An ordinary, honest, liveable
flat is a 60-75; reserve 85+ for one you would tell a friend to call about immediately, and
go under 40 when something is genuinely wrong with it."""


class Verdict(BaseModel):
    """What the model returns for one listing."""

    fit: int = Field(ge=0, le=100, description="How well this fits the stated preferences")
    verdict: str = Field(description="One sentence, plain and specific, max 200 characters")
    condition: str = Field(description="One of: renovated, good, dated, poor, unclear")
    pros: list[str] = Field(default_factory=list, description="Up to 3 short phrases")
    cons: list[str] = Field(default_factory=list, description="Up to 3 short phrases")
    red_flags: list[str] = Field(
        default_factory=list, description="Possible scams, hidden costs, or misleading photos"
    )


def _facts(listing: Listing) -> str:
    if listing.is_agency:
        who = " (agency)"
    elif listing.is_agency is False:
        who = " (private owner)"
    else:
        who = ""
    rows = [
        f"Source: {listing.source}",
        f"Title: {listing.title}",
        f"Price: {listing.price_eur} EUR/month" if listing.price_eur else "Price: unknown",
        f"Area: {listing.area_m2} m2" if listing.area_m2 else "Area: unknown",
        f"EUR per m2: {listing.price_per_m2}" if listing.price_per_m2 else "",
        f"Rooms: {listing.rooms}" if listing.rooms else "",
        f"Floor: {listing.floor} of {listing.total_floors}" if listing.floor is not None else "",
        f"Location: {listing.location_text}",
        f"Advertiser: {listing.advertiser or 'unknown'}{who}",
        f"Photos attached: {len(listing.images)}",
        "",
        "Description (Serbian, verbatim):",
        listing.description or "(none given)",
    ]
    return "\n".join(r for r in rows if r)


class MissingCredentials(RuntimeError):
    """No usable Anthropic credential; raised at startup rather than per listing."""


class Grader:
    def __init__(self, cfg: LlmConfig):
        self.cfg = cfg
        # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile.
        self.client = anthropic.AsyncAnthropic()
        # The client constructs happily without a credential and only fails when the first
        # request is built, which would otherwise surface as a per-listing error hours
        # later. Check once, here, so startup can disable grading with a clear message.
        if not (self.client.api_key or self.client.auth_token):
            raise MissingCredentials(
                "no Anthropic credential found. Set ANTHROPIC_API_KEY in .env, run "
                "`ant auth login`, or set scoring.llm.enabled: false in config.yaml."
            )

    async def grade(self, listing: Listing, preferences: str) -> Verdict | None:
        """Score one listing. Returns None if the call fails - never raises into the loop."""
        content: list[dict] = []
        for url in listing.images[: self.cfg.max_images]:
            content.append({"type": "image", "source": {"type": "url", "url": url}})

        prompt = f"{_facts(listing)}\n\nWhat this person is looking for:\n{preferences}"
        if self.cfg.notes:
            prompt += f"\n\nExtra context from them:\n{self.cfg.notes}"
        content.append({"type": "text", "text": prompt})

        try:
            response = await self.client.messages.parse(
                model=self.cfg.model,
                max_tokens=2000,
                system=SYSTEM,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": self.cfg.effort},
                output_format=Verdict,
            )
        except anthropic.RateLimitError:
            log.warning("llm: rate limited, skipping %s", listing.key)
            return None
        except anthropic.APIStatusError as exc:
            # A broken image URL or an unsupported media type lands here; log and move on.
            log.warning("llm: %s for %s (%s)", exc.status_code, listing.key, exc.message)
            return None
        except anthropic.APIConnectionError as exc:
            log.warning("llm: connection error for %s (%s)", listing.key, exc)
            return None
        except Exception as exc:
            # Scoring is optional enrichment on top of the rule score. Whatever goes wrong
            # here - a bad image URL, a schema change, a credential expiring mid-run - must
            # never take down the poll loop, which is the part that cannot miss a listing.
            log.warning("llm: unexpected error for %s (%r)", listing.key, exc)
            return None

        if response.stop_reason == "refusal":
            log.warning("llm: refused %s", listing.key)
            return None
        return response.parsed_output
