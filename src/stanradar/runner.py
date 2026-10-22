"""The polling loop.

One cycle per source, all sources concurrently, then sleep. The pipeline for each listing:

    fetch list -> already seen? -> rule filter -> fetch detail -> rule filter again
      -> LLM grade (only if promising) -> threshold -> Discord -> record

The detail fetch sits *after* the first rule pass on purpose: a listing that is out of
budget costs one row in the list response and nothing more. The second rule pass exists
because the detail page is where the description, the real photo set, and the owner-vs-agency
fact arrive - things that can disqualify a listing the summary made look fine.

Several *profiles* run against the same fetched listings - one search for yourself, another
for a three-bedroom share, say. Fetching is the rate-limited part, so it happens once and
each profile filters the same results independently. A listing is enriched at most once no
matter how many profiles want it, and a profile that rejects it costs nothing further.

First run per source is a *priming* run: everything currently listed is recorded as seen
without alerting, so starting the app does not dump 300 historical listings into Discord.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

from .config import Config, Profile
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


@dataclass
class ProfileRuntime:
    """One configured search, plus the things it needs to act on a match."""

    profile: Profile
    discord: Discord
    preferences: str
    grader: Grader | None = None

    @property
    def name(self) -> str:
        return self.profile.name


@dataclass
class _Outcome:
    """What one profile decided about one listing."""

    rules: RuleResult
    verdict: Verdict | None = None
    alerted: bool = False
    duplicate: bool = False

    @property
    def score(self) -> int:
        return self.verdict.fit if self.verdict else self.rules.score


@dataclass
class _Pipeline:
    outcomes: dict[str, _Outcome] = field(default_factory=dict)

    def best_score(self) -> int | None:
        scored = [o.score for o in self.outcomes.values() if o.rules.passed]
        return max(scored) if scored else None

    def any_alerted(self) -> bool:
        return any(o.alerted for o in self.outcomes.values())

    def rejection_summary(self) -> str:
        """Why every profile said no, for the `recent` view."""
        return "; ".join(
            f"{name}: {o.rules.rejected_for}"
            for name, o in self.outcomes.items()
            if not o.rules.passed and o.rules.rejected_for
        )[:200]


class Runner:
    def __init__(
        self,
        cfg: Config,
        sources: list[Source],
        store: Store,
        profiles: list[ProfileRuntime],
        dry_run: bool = False,
    ):
        self.cfg = cfg
        self.sources = sources
        self.store = store
        self.profiles = profiles
        self.dry_run = dry_run

    async def run_forever(self) -> None:
        log.info(
            "watching %s every %ds for %s",
            ", ".join(s.label for s in self.sources),
            self.cfg.poll_interval_seconds,
            ", ".join(p.name for p in self.profiles),
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
            for name in total.__dataclass_fields__:
                setattr(total, name, getattr(total, name) + getattr(result, name))
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
                await self.profiles[0].discord.send_text(
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
            listing, detail_budget = await self._maybe_enrich(source, listing, detail_budget)
            pipeline = await self._run_profiles(listing, stats)
            self.store.record(
                listing,
                rule_score=pipeline.best_score(),
                llm_score=max(
                    (o.verdict.fit for o in pipeline.outcomes.values() if o.verdict),
                    default=None,
                ),
                notified=pipeline.any_alerted(),
                rejected_for=None if pipeline.any_alerted() else pipeline.rejection_summary(),
            )
        return stats

    async def _maybe_enrich(
        self, source: Source, listing: Listing, budget: int
    ) -> tuple[Listing, int]:
        """Fetch the detail page once, if any profile could plausibly want this listing.

        The cheap pass runs first so a listing no profile would take never costs a request.
        """
        if not source.has_detail_fetch or budget <= 0:
            return listing, budget

        plausible = any(
            evaluate(listing, rt.profile.criteria, full_detail=False).passed
            for rt in self.profiles
        )
        if not plausible:
            return listing, budget

        try:
            listing = await source.enrich(listing)
            budget -= 1
        except Exception as exc:
            log.debug("%s: enrich failed (%s)", listing.key, exc)
        return listing, budget

    async def _run_profiles(self, listing: Listing, stats: CycleStats) -> _Pipeline:
        pipeline = _Pipeline()
        for runtime in self.profiles:
            outcome = await self._run_profile(runtime, listing, stats)
            pipeline.outcomes[runtime.name] = outcome
        return pipeline

    async def _run_profile(
        self, runtime: ProfileRuntime, listing: Listing, stats: CycleStats
    ) -> _Outcome:
        rules = evaluate(listing, runtime.profile.criteria)
        if not rules.passed:
            log.debug("%s [%s]: rejected (%s)", listing.key, runtime.name, rules.rejected_for)
            return _Outcome(rules)

        stats.passed_rules += 1
        verdict = await self._grade(runtime, listing, rules)
        if verdict:
            stats.graded += 1

        outcome = _Outcome(rules, verdict)
        if outcome.score < runtime.profile.scoring.alert_threshold:
            return outcome

        duplicate = self.store.duplicate_of(listing)
        if duplicate:
            stats.duplicates += 1
            log.info("%s: same flat as %s, suppressing", listing.key, duplicate)
            outcome.duplicate = True
            return outcome

        # Weaker cross-portal match: worth mentioning, never worth suppressing.
        also_at = self.store.similar_on_another_portal(listing)

        if self.dry_run:
            log.info(
                "MATCH (dry run) [%s] %s | %s EUR | %s m2 | %s rooms | %s | score %s%s\n  %s",
                runtime.name, listing.source, listing.price_eur, listing.area_m2,
                listing.rooms, listing.location_text, outcome.score,
                f" | also at {also_at}" if also_at else "",
                listing.url,
            )
        else:
            await runtime.discord.send_listing(
                listing, rules, verdict, duplicate_of=also_at, profile=runtime.name
            )
        outcome.alerted = True
        stats.alerted += 1
        return outcome

    async def _grade(
        self, runtime: ProfileRuntime, listing: Listing, rules: RuleResult
    ) -> Verdict | None:
        llm = runtime.profile.scoring.llm
        if not (runtime.grader and llm.enabled):
            return None
        if rules.score < llm.min_rule_score:
            return None
        if not listing.images:
            # Nothing to look at; the rule score already covers the numbers.
            return None
        return await runtime.grader.grade(listing, runtime.preferences)

    async def _prime(self, source: Source, listings: list[Listing]) -> None:
        """Record what is already listed without alerting on it."""
        matches: dict[str, int] = {rt.name: 0 for rt in self.profiles}
        for listing in listings:
            best: int | None = None
            reasons = []
            for runtime in self.profiles:
                rules = evaluate(
                    listing, runtime.profile.criteria, full_detail=not source.has_detail_fetch
                )
                if rules.passed:
                    matches[runtime.name] += 1
                    best = rules.score if best is None else max(best, rules.score)
                elif rules.rejected_for:
                    reasons.append(f"{runtime.name}: {rules.rejected_for}")
            self.store.record(
                listing,
                rule_score=best,
                rejected_for=None if best is not None else "; ".join(reasons)[:200],
            )
        self.store.mark_primed(source.name)
        summary = ", ".join(f"{n} for {name}" for name, n in matches.items())
        log.info(
            "%s: primed with %d existing listings (%s) - alerts start from the next new one",
            source.name, len(listings), summary,
        )
        if not self.dry_run:
            await self.profiles[0].discord.send_text(
                f":satellite: Now watching **{source.label}**. "
                f"{len(listings)} current listings recorded as already-seen "
                f"({summary}). Alerts start with the next new post."
            )
