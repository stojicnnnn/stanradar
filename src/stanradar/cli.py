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
from .notify import Discord
from .runner import Runner
from .scoring import evaluate
from .sources import REGISTRY, ClientPool, build
from .store import Store

log = logging.getLogger("stanradar")


def _preferences(cfg: Config) -> str:
    """A plain-language summary of the criteria, for the LLM prompt."""
    c = cfg.criteria
    lines = []
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

    discord = Discord(cfg.discord, pool.plain)
    if not discord.configured and not args.dry_run:
        log.error(
            "no Discord webhook. Set DISCORD_WEBHOOK_URL in .env, or pass --dry-run to "
            "print matches to the console instead."
        )
        return 2

    grader = None
    if cfg.scoring.llm.enabled:
        try:
            grader = Grader(cfg.scoring.llm)
        except MissingCredentials as exc:
            log.warning("AI scoring off: %s", exc)
            log.warning("Continuing with rule scores only - alerts still work.")
        except Exception as exc:
            log.warning("AI scoring off (%r); continuing with rule scores only", exc)

    runner = Runner(cfg, sources, store, discord, grader, _preferences(cfg), args.dry_run)
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
    discord = Discord(cfg.discord, pool.plain)
    if not discord.configured:
        log.error("no Discord webhook configured (DISCORD_WEBHOOK_URL)")
        await pool.aclose()
        return 2
    await discord.send_text(f":white_check_mark: stanradar {__version__} is wired up correctly.")
    await pool.aclose()
    log.info("sent a test message")
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
            rules = evaluate(listing, cfg.criteria, full_detail=not source.has_detail_fetch)
            mark = f"PASS {rules.score:>3}" if rules.passed else f"skip ({rules.rejected_for})"
            age = listing.age_minutes()
            age_text = f"{age:.0f} min ago" if age is not None else "age unknown"
            print(
                f"  [{mark}] {listing.price_eur or '?':>5} EUR  "
                f"{listing.area_m2 or '?':>5} m2  {listing.location_text[:40]:<40} "
                f"{age_text}"
            )
            print(f"          {listing.url}")
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

    if args.command is None:
        args.command = "watch"
        args.once = False
        args.dry_run = False
    return asyncio.run(_watch(args, cfg))


if __name__ == "__main__":
    sys.exit(main())
