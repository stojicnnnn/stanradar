"""SQLite de-duplication and alert history.

De-duplication happens at two levels:
  * `key`         - the same ad seen again on the same portal (every poll cycle).
  * `fingerprint` - the same flat re-listed under the same district name. Suppressed for
                    `fingerprint_ttl_hours` so repeated agency re-posts give you one ping.
  * `loose_fp`    - the same flat seen on a *different* portal under a different district
                    name. Only annotates the alert; never suppresses it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key           TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    loose_fp      TEXT,
    url           TEXT NOT NULL,
    title         TEXT,
    price_eur     INTEGER,
    area_m2       REAL,
    rooms         REAL,
    district      TEXT,
    posted_at     TEXT,
    first_seen    TEXT NOT NULL,
    rule_score    INTEGER,
    llm_score     INTEGER,
    notified_at   TEXT,
    rejected_for  TEXT,
    payload       TEXT
);
CREATE INDEX IF NOT EXISTS idx_fingerprint ON listings(fingerprint);
CREATE INDEX IF NOT EXISTS idx_loose_fp     ON listings(loose_fp);
CREATE INDEX IF NOT EXISTS idx_first_seen  ON listings(first_seen);
CREATE TABLE IF NOT EXISTS source_state (
    source     TEXT PRIMARY KEY,
    primed_at  TEXT,
    last_ok    TEXT,
    last_error TEXT,
    fail_count INTEGER DEFAULT 0
);
"""


class Store:
    def __init__(self, path: str, fingerprint_ttl_hours: int = 72):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(p, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.fingerprint_ttl = timedelta(hours=fingerprint_ttl_hours)

    def close(self) -> None:
        self.db.close()

    # --- de-duplication -------------------------------------------------

    def is_known(self, listing: Listing) -> bool:
        row = self.db.execute("SELECT 1 FROM listings WHERE key = ?", (listing.key,)).fetchone()
        return row is not None

    def duplicate_of(self, listing: Listing) -> str | None:
        """URL of a recent listing that is confidently the same flat. Safe to suppress."""
        cutoff = (datetime.now(UTC) - self.fingerprint_ttl).isoformat()
        row = self.db.execute(
            """SELECT url FROM listings
               WHERE fingerprint = ? AND key != ? AND first_seen >= ?
               ORDER BY first_seen DESC LIMIT 1""",
            (listing.fingerprint(), listing.key, cutoff),
        ).fetchone()
        return row["url"] if row else None

    def similar_on_another_portal(self, listing: Listing) -> str | None:
        """URL of a recent listing on a *different* portal with the same price, size and
        rooms. Too weak to suppress on, so the caller only mentions it in the alert."""
        loose = listing.loose_fingerprint()
        if not loose:
            return None
        cutoff = (datetime.now(UTC) - self.fingerprint_ttl).isoformat()
        row = self.db.execute(
            """SELECT url FROM listings
               WHERE loose_fp = ? AND source != ? AND first_seen >= ?
               ORDER BY first_seen DESC LIMIT 1""",
            (loose, listing.source, cutoff),
        ).fetchone()
        return row["url"] if row else None

    # --- writes ---------------------------------------------------------

    def record(
        self,
        listing: Listing,
        rule_score: int | None = None,
        llm_score: int | None = None,
        notified: bool = False,
        rejected_for: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            """INSERT INTO listings
               (key, source, external_id, fingerprint, loose_fp, url, title, price_eur,
                area_m2, rooms, district, posted_at, first_seen, rule_score, llm_score,
                notified_at, rejected_for, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 rule_score  = excluded.rule_score,
                 llm_score   = excluded.llm_score,
                 notified_at = COALESCE(listings.notified_at, excluded.notified_at)""",
            (
                listing.key,
                listing.source,
                listing.external_id,
                listing.fingerprint(),
                listing.loose_fingerprint(),
                listing.url,
                listing.title,
                listing.price_eur,
                listing.area_m2,
                listing.rooms,
                listing.district,
                listing.posted_at.isoformat() if listing.posted_at else None,
                now,
                rule_score,
                llm_score,
                now if notified else None,
                rejected_for,
                json.dumps(listing.raw, ensure_ascii=False)[:20000],
            ),
        )
        self.db.commit()

    # --- per-source bookkeeping ----------------------------------------

    def is_primed(self, source: str) -> bool:
        row = self.db.execute(
            "SELECT primed_at FROM source_state WHERE source = ?", (source,)
        ).fetchone()
        return bool(row and row["primed_at"])

    def mark_primed(self, source: str) -> None:
        self.db.execute(
            """INSERT INTO source_state (source, primed_at) VALUES (?, ?)
               ON CONFLICT(source) DO UPDATE SET primed_at = excluded.primed_at""",
            (source, datetime.now(UTC).isoformat()),
        )
        self.db.commit()

    def mark_result(self, source: str, error: str | None) -> int:
        """Record success or failure; returns the consecutive failure count."""
        now = datetime.now(UTC).isoformat()
        if error is None:
            self.db.execute(
                """INSERT INTO source_state (source, last_ok, fail_count) VALUES (?, ?, 0)
                   ON CONFLICT(source) DO UPDATE SET
                     last_ok = excluded.last_ok, fail_count = 0, last_error = NULL""",
                (source, now),
            )
            self.db.commit()
            return 0
        self.db.execute(
            """INSERT INTO source_state (source, last_error, fail_count) VALUES (?, ?, 1)
               ON CONFLICT(source) DO UPDATE SET
                 last_error = excluded.last_error,
                 fail_count = source_state.fail_count + 1""",
            (source, error[:500]),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT fail_count FROM source_state WHERE source = ?", (source,)
        ).fetchone()
        return int(row["fail_count"]) if row else 1

    def stats(self) -> dict[str, int]:
        row = self.db.execute(
            "SELECT COUNT(*) AS total, SUM(notified_at IS NOT NULL) AS notified FROM listings"
        ).fetchone()
        return {"total": row["total"] or 0, "notified": row["notified"] or 0}

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT url, title, price_eur, area_m2, district, rule_score, llm_score,
                      notified_at, rejected_for
               FROM listings ORDER BY first_seen DESC LIMIT ?""",
            (limit,),
        ).fetchall()
