"""The polling loop.

One cycle per source, all sources concurrently, then sleep. The pipeline for each listing:

    fetch list -> already seen? -> rule filter -> fetch detail -> rule filter again
      -> LLM grade (only if promising) -> threshold -> Discord -> record

The detail fetch sits *after* the first rule pass on purpose: a listing that is out of
budget costs one row in the list response and nothing more. The second rule pass exists
because the detail page is where the description, the real photo set, and the owner-vs-agency
fact arrive - things that can disqualify a listing the summary made look fine.

First run per source is a *priming* run: everything currently listed is recorded as seen
without alerting, so starting the app does not dump 300 historical listings into Discord.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass

from .config import Config
from .llm import Grader, Verdict
from .models import Listing
from .notify import Discord
from .scoring import RuleResult, evaluate
from .sources import Source
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class CycleStats:
    fetched: int = 0
    new: int = 0
    passed_rules: int = 0
    graded: int = 0
    alerted: int = 0
    duplicates: int = 0
    errors: int = 0


class Runner:
    def __init__(
        self,
        cfg: Config,
        sources: list[Source],
        store: Store,
        discord: Discord,
        grader: Grader | None,
        preferences: str,
        dry_run: bool = False,
    ):
        self.cfg = cfg
        self.sources = sources
        self.store = store
        self.discord = discord
        self.grader = grader
        self.preferences = preferences
        self.dry_run = dry_run

    async def run_forever(self) -> None:
        log.info(
            "watching %s every %ds",
            ", ".join(s.label for s in self.sources),
            self.cfg.poll_interval_seconds,
        )
        while True:
            stats = await self.run_cycle()
            log.info(
                "cycle: %d fetched, %d new, %d passed, %d graded, %d alerted, "
                "%d dupes, %d errors",
                stats.fetched, stats.new, stats.passed_rules, stats.graded,
                stats.alerted, stats.duplicates, stats.errors,
            )
            # Jitter so repeated runs don't hammer every portal on the same second.
            await asyncio.sleep(self.cfg.poll_interval_seconds + random.uniform(0, 5))

    async def run_cycle(self) -> CycleStats:
        results = await asyncio.gather(
            *(self._poll_source(s) for s in self.sources), return_exceptions=True
        )
        total = CycleStats()
        # strict=True: gather preserves order and length, and a mismatch would silently
        # attribute one source's failure to another.
        for source, result in zip(self.sources, results, strict=True):
            if isinstance(result, BaseException):
                log.exception("%s: cycle failed", source.name, exc_info=result)
                total.errors += 1
                continue
            for field in total.__dataclass_fields__:
                setattr(total, field, getattr(total, field) + getattr(result, field))
        return total

    async def _poll_source(self, source: Source) -> CycleStats:
        stats = CycleStats()
        try:
            listings = await source.fetch_new()
        except Exception as exc:
            failures = self.store.mark_result(source.name, repr(exc))
            log.warning("%s: fetch failed (%d in a row): %s", source.name, failures, exc)
            # Tell me once when a portal has been down long enough to matter.
            if failures == 5 and not self.dry_run:
                await self.discord.send_text(
                    f":warning: `{source.label}` has failed {failures} polls in a row: `{exc}`"
                )
            stats.errors = 1
            return stats

        self.store.mark_result(source.name, None)
        stats.fetched = len(listings)

        if not self.store.is_primed(source.name):
            await self._prime(source, listings)
            return stats

        detail_budget = self.cfg.max_detail_fetches_per_cycle
        for listing in listings:
            if self.store.is_known(listing):
                continue
            stats.new += 1

            # On the first pass, defer the rules that need data only the detail page has.
            rules = evaluate(
                listing, self.cfg.criteria, full_detail=not source.has_detail_fetch
            )
            if not rules.passed:
                log.debug("%s: rejected (%s)", listing.key, rules.rejected_for)
                self.store.record(listing, rejected_for=rules.rejected_for)
                continue

            # Promising so far - now it is worth a detail request.
            if source.has_detail_fetch and detail_budget > 0:
                try:
                    listing = await source.enrich(listing)
                    detail_budget -= 1
                except Exception as exc:
                    log.debug("%s: enrich failed (%s)", listing.key, exc)
                rules = evaluate(listing, self.cfg.criteria)
                if not rules.passed:
                    self.store.record(listing, rejected_for=rules.rejected_for)
                    continue

            stats.passed_rules += 1

            verdict = await self._grade(listing, rules)
            if verdict:
                stats.graded += 1

            score = verdict.fit if verdict else rules.score
            if score < self.cfg.scoring.alert_threshold:
                self.store.record(
                    listing,
                    rule_score=rules.score,
                    llm_score=verdict.fit if verdict else None,
                    rejected_for=f"score {score}",
                )
                continue

            duplicate = self.store.duplicate_of(listing)
            if duplicate:
                stats.duplicates += 1
                log.info("%s: same flat as %s, suppressing", listing.key, duplicate)
                self.store.record(
                    listing, rule_score=rules.score,
                    llm_score=verdict.fit if verdict else None,
                    notified=True, rejected_for="duplicate",
                )
                continue

            # Weaker cross-portal match: worth mentioning, never worth suppressing.
            also_at = self.store.similar_on_another_portal(listing)

            if self.dry_run:
                log.info(
                    "MATCH (dry run) %s | %s EUR | %s m2 | %s | score %s%s\n  %s",
                    listing.source, listing.price_eur, listing.area_m2,
                    listing.location_text, score,
                    f" | also at {also_at}" if also_at else "",
                    listing.url,
                )
            else:
                await self.discord.send_listing(listing, rules, verdict, duplicate_of=also_at)
            stats.alerted += 1
            self.store.record(
                listing, rule_score=rules.score,
                llm_score=verdict.fit if verdict else None, notified=True,
            )
        return stats

    async def _grade(self, listing: Listing, rules: RuleResult) -> Verdict | None:
        llm = self.cfg.scoring.llm
        if not (self.grader and llm.enabled):
            return None
        if rules.score < llm.min_rule_score:
            return None
        if not listing.images:
            # Nothing to look at; the rule score already covers the numbers.
            return None
        return await self.grader.grade(listing, self.preferences)

    async def _prime(self, source: Source, listings: list[Listing]) -> None:
        """Record what is already listed without alerting on it."""
        matches = 0
        for listing in listings:
            rules = evaluate(
                listing, self.cfg.criteria, full_detail=not source.has_detail_fetch
            )
            if rules.passed:
                matches += 1
            self.store.record(
                listing,
                rule_score=rules.score if rules.passed else None,
                rejected_for=None if rules.passed else rules.rejected_for,
            )
        self.store.mark_primed(source.name)
        log.info(
            "%s: primed with %d existing listings (%d would have matched) - "
            "alerts start from the next new one",
            source.name, len(listings), matches,
        )
        if not self.dry_run:
            await self.discord.send_text(
                f":satellite: Now watching **{source.label}**. "
                f"{len(listings)} current listings recorded as already-seen "
                f"({matches} match your criteria). Alerts start with the next new post."
            )
