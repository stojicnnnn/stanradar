"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .config import Config
from .llm import Grader, MissingCredentials
from .models import Listing
from .notify import Discord
from .runner import ProfileRuntime, Runner
from .scoring import evaluate
from .sources import REGISTRY, ClientPool, build
from .store import Store

log = logging.getLogger("stanradar")


def _preferences(criteria) -> str:
    """A plain-language summary of the criteria, for the LLM prompt."""
    c = criteria
    lines = []
    if c.property_types != ["apartment"]:
        lines.append("Considering: " + " and ".join(c.property_types) + "s")
    if c.bedrooms.min is not None:
        lines.append(
            f"At least {c.bedrooms.min:g} separate bedrooms "
            f"(so at least a {c.bedrooms.min + 1:g}-room / "
            f"{'cetvorosoban' if c.bedrooms.min >= 3 else 'trosoban'}-style place)"
        )
    if c.price_eur.min is not None or c.price_eur.max is not None:
        lines.append(f"Budget: {c.price_eur.min or 0}-{c.price_eur.max or 'any'} EUR per month")
    if c.area_m2.min is not None:
        area = f"At least {c.area_m2.min:g} m2"
        if c.area_m2.max:
            area += f", at most {c.area_m2.max:g}"
        lines.append(area)
    if c.rooms.min is not None:
        lines.append(f"At least {c.rooms.min:g} rooms")
    if c.floor.min is not None:
        lines.append(f"Floor {c.floor.min:g} or above")
    if c.districts.preferred:
        lines.append("Preferred areas: " + ", ".join(c.districts.preferred))
    if c.districts.acceptable:
        lines.append("Also fine: " + ", ".join(c.districts.acceptable))
    if c.districts.excluded:
        lines.append("Will not consider: " + ", ".join(c.districts.excluded))
    if c.keywords.bonus:
        lines.append("Wants if possible: " + ", ".join(c.keywords.bonus))
    if c.keywords.blocked:
        lines.append("Deal breakers: " + ", ".join(c.keywords.blocked))
    if c.owners_only is True:
        lines.append("Prefers dealing directly with owners, not agencies")
    return "\n".join(f"- {line}" for line in lines)


async def _watch(args: argparse.Namespace, cfg: Config) -> int:
    store = Store(cfg.database_path, cfg.fingerprint_ttl_hours)
    pool = ClientPool(cfg.request_timeout_seconds)
    try:
        sources = build(cfg.sources, pool, cfg)
    except ValueError as exc:
        log.error("%s", exc)
        await pool.aclose()
        return 2

    if not any(p.discord.webhook_url for p in cfg.profiles) and not args.dry_run:
        log.error(
            "no Discord webhook. Set DISCORD_WEBHOOK_URL in .env, or pass --dry-run to "
            "print matches to the console instead."
        )
        await pool.aclose()
        store.close()
        return 2

    # One grader per distinct model, shared by every profile that asks for it.
    graders: dict[str, Grader] = {}
    for model in {p.scoring.llm.model for p in cfg.profiles if p.scoring.llm.enabled}:
        try:
            graders[model] = Grader(
                next(p.scoring.llm for p in cfg.profiles if p.scoring.llm.model == model)
            )
        except MissingCredentials as exc:
            log.warning("AI scoring off: %s", exc)
            log.warning("Continuing with rule scores only - alerts still work.")
        except Exception as exc:
            log.warning("AI scoring off (%r); continuing with rule scores only", exc)

    runtimes = []
    for profile in cfg.profiles:
        grader = graders.get(profile.scoring.llm.model) if profile.scoring.llm.enabled else None
        # Each profile may point at a different channel, so give it its own notifier.
        profile_discord = Discord(profile.discord, pool.plain)
        if not profile_discord.configured and not args.dry_run:
            log.error("profile %r has no Discord webhook configured", profile.name)
            await pool.aclose()
            store.close()
            return 2
        runtimes.append(
            ProfileRuntime(
                profile=profile,
                discord=profile_discord,
                preferences=_preferences(profile.criteria),
                grader=grader,
            )
        )
    log.info("profiles: %s", ", ".join(p.name for p in cfg.profiles))

    runner = Runner(cfg, sources, store, runtimes, args.dry_run)
    try:
        if args.once:
            stats = await runner.run_cycle()
            log.info("%s", stats)
            # Exit non-zero when every source failed, so a scheduled CI run shows red
            # instead of passing quietly. One portal being down is normal and stays
            # green; all of them failing usually means the runner's IP is blocked.
            if sources and stats.errors == len(sources):
                log.error("every source failed this cycle")
                return 1
        else:
            await runner.run_forever()
    except KeyboardInterrupt:
        log.info("stopped")
    finally:
        await pool.aclose()
        store.close()
    return 0


async def _test_discord(cfg: Config) -> int:
    pool = ClientPool(cfg.request_timeout_seconds)
    discord = Discord(cfg.profiles[0].discord, pool.plain)
    if not discord.configured:
        log.error("no Discord webhook configured (DISCORD_WEBHOOK_URL)")
        await pool.aclose()
        return 2
    await discord.send_text(f":white_check_mark: stanradar {__version__} is wired up correctly.")
    await pool.aclose()
    log.info("sent a test message")
    return 0


async def _scan(args: argparse.Namespace, cfg: Config) -> int:
    """Show - and optionally send - everything currently listed that matches a profile.

    Distinct from `watch`, which only ever reports listings it has not seen before. This
    deliberately ignores the seen-state so you can ask "what is live right now", and it
    never records anything, so it cannot cause the watcher to miss a later alert.
    """
    wanted = [p for p in cfg.profiles if args.profile in (None, p.name)]
    if not wanted:
        log.error(
            "no profile named %r; available: %s",
            args.profile, ", ".join(p.name for p in cfg.profiles),
        )
        return 2

    pool = ClientPool(cfg.request_timeout_seconds)
    try:
        sources = build(cfg.sources, pool, cfg, pages=args.pages)
    except ValueError as exc:
        log.error("%s", exc)
        await pool.aclose()
        return 2

    log.info(
        "scanning %s for %s (%d page%s per source)",
        ", ".join(s.label for s in sources),
        ", ".join(p.name for p in wanted),
        args.pages, "" if args.pages == 1 else "s",
    )

    listings: list[Listing] = []
    for source in sources:
        try:
            found = await source.fetch_new(limit=args.pages * 40)
            listings.extend(found)
            log.info("  %s: %d listings", source.label, len(found))
        except Exception as exc:
            log.warning("  %s: failed (%s)", source.label, exc)

    graders: dict[str, Grader] = {}
    for model in {p.scoring.llm.model for p in wanted if p.scoring.llm.enabled}:
        try:
            graders[model] = Grader(
                next(p.scoring.llm for p in wanted if p.scoring.llm.model == model)
            )
        except Exception as exc:
            log.warning("AI scoring off (%s); using rule scores only", exc)

    sent = 0
    seen_flats: set[str] = set()
    for profile in wanted:
        matches = []
        for listing in listings:
            # Cheap pass first, so a detail fetch is never spent on a listing we'd drop.
            if not evaluate(listing, profile.criteria, full_detail=False).passed:
                continue
            matches.append(listing)

        log.info("[%s] %d candidates before detail", profile.name, len(matches))
        confirmed = []
        for listing in matches:
            source = next((s for s in sources if s.name == listing.source), None)
            if source is not None and source.has_detail_fetch:
                try:
                    listing = await source.enrich(listing)
                except Exception as exc:
                    log.debug("enrich failed for %s (%s)", listing.key, exc)
            rules = evaluate(listing, profile.criteria)
            if rules.passed:
                confirmed.append((listing, rules))

        confirmed.sort(key=lambda pair: pair[1].score, reverse=True)
        print(f"\n=== {profile.name}: {len(confirmed)} live match(es)")
        discord = Discord(profile.discord, pool.plain)
        for listing, rules in confirmed:
            # The same flat is often cross-posted; show it once.
            flat = listing.loose_fingerprint() or listing.key
            duplicate = flat in seen_flats
            seen_flats.add(flat)

            beds = listing.bedroom_count()
            print(
                f"  [{rules.score:>3}] {listing.price_eur or '?':>5} EUR "
                f"{listing.area_m2 or '?':>6} m2 "
                f"{(f'{beds:g}bed' if beds is not None else '?bed'):>5} "
                f"{'house' if listing.property_type == 'house' else 'flat ':<5} "
                f"{listing.location_text[:38]:<38}"
                + ("  [dupe]" if duplicate else "")
            )
            print(f"        {listing.url}")

            if args.send and not duplicate:
                verdict = None
                grader = graders.get(profile.scoring.llm.model)
                if grader and profile.scoring.llm.enabled and listing.images:
                    verdict = await grader.grade(listing, _preferences(profile.criteria))
                await discord.send_listing(listing, rules, verdict, profile=profile.name)
                sent += 1

    if args.send:
        log.info("sent %d listing(s) to Discord", sent)
    else:
        log.info("nothing sent - add --send to post these to Discord")
    await pool.aclose()
    return 0


async def _probe(cfg: Config) -> int:
    """Fetch one page from each configured source and show what the rules make of it."""
    pool = ClientPool(cfg.request_timeout_seconds)
    try:
        sources = build(cfg.sources, pool, cfg)
    except ValueError as exc:
        log.error("%s", exc)
        await pool.aclose()
        return 2

    for source in sources:
        print(f"\n=== {source.label}")
        try:
            listings = await source.fetch_new(limit=10)
        except Exception as exc:
            print(f"  FAILED: {exc!r}")
            continue
        print(f"  {len(listings)} listings")
        for listing in listings:
            verdicts = []
            for profile in cfg.profiles:
                rules = evaluate(
                    listing, profile.criteria, full_detail=not source.has_detail_fetch
                )
                verdicts.append(
                    f"{profile.name}="
                    + (f"PASS {rules.score}" if rules.passed else f"skip({rules.rejected_for})")
                )
            age = listing.age_minutes()
            age_text = f"{age:.0f} min ago" if age is not None else "age unknown"
            kind = "house" if listing.property_type == "house" else "flat"
            beds = listing.bedroom_count()
            print(
                f"  {listing.price_eur or '?':>5} EUR {listing.area_m2 or '?':>5} m2 "
                f"{(f'{beds:g}bed' if beds is not None else '?bed'):>5} {kind:<5} "
                f"{listing.location_text[:34]:<34} {age_text}"
            )
            print(f"        {' | '.join(verdicts)}")
            print(f"        {listing.url}")
    await pool.aclose()
    return 0


def _list_sources() -> int:
    print("Sources:")
    for name, cls in sorted(REGISTRY.items()):
        if cls.implemented:
            print(f"  {name:<16} {cls.label:<22} ready")
        else:
            print(f"  {name:<16} {cls.label:<22} NOT IMPLEMENTED - {cls.reason}")
    return 0


def _show_recent(cfg: Config, limit: int) -> int:
    store = Store(cfg.database_path, cfg.fingerprint_ttl_hours)
    stats = store.stats()
    print(f"{stats['total']} listings seen, {stats['notified']} alerted\n")
    for row in store.recent(limit):
        flag = "ALERT" if row["notified_at"] else (row["rejected_for"] or "")
        print(
            f"{row['price_eur'] or '?':>5} EUR {row['area_m2'] or '?':>5} m2  "
            f"{(row['district'] or '')[:18]:<18} rules={row['rule_score'] or '-':<4} "
            f"ai={row['llm_score'] or '-':<4} {flag[:28]:<28} {row['url']}"
        )
    store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stanradar", description="Watch Belgrade rental portals and alert on Discord."
    )
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    watch = sub.add_parser("watch", help="poll forever (the normal mode)")
    watch.add_argument("--once", action="store_true", help="run a single cycle and exit")
    watch.add_argument(
        "--dry-run", action="store_true", help="print matches instead of posting to Discord"
    )

    sub.add_parser("probe", help="fetch one page per source and show how rules score it")
    scan_cmd = sub.add_parser(
        "scan", help="show everything currently listed that matches, ignoring seen-state"
    )
    scan_cmd.add_argument("-p", "--profile", help="only this profile (default: all)")
    scan_cmd.add_argument(
        "--pages", type=int, default=5, help="pages to sweep where the source supports it"
    )
    scan_cmd.add_argument("--send", action="store_true", help="post the matches to Discord")
    sub.add_parser("sources", help="list available sources")
    sub.add_parser("test-discord", help="send a test message to the webhook")
    recent = sub.add_parser("recent", help="show recently seen listings from the database")
    recent.add_argument("-n", "--limit", type=int, default=20)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "sources":
        return _list_sources()

    load_dotenv()
    config_path = Path(args.config)
    if not config_path.exists():
        log.error(
            "no config at %s. Copy config.example.yaml to config.yaml and edit it.",
            config_path,
        )
        return 2
    cfg = Config.load(config_path)

    if args.command == "recent":
        return _show_recent(cfg, args.limit)
    if args.command == "test-discord":
        return asyncio.run(_test_discord(cfg))
    if args.command == "probe":
        return asyncio.run(_probe(cfg))
    if args.command == "scan":
        return asyncio.run(_scan(args, cfg))

    if args.command is None:
        args.command = "watch"
        args.once = False
        args.dry_run = False
    return asyncio.run(_watch(args, cfg))


if __name__ == "__main__":
    sys.exit(main())
