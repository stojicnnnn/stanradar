"""Typed config loaded from config.yaml, with ${ENV_VAR} expansion."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")


class Range(BaseModel):
    min: float | None = None
    max: float | None = None

    def contains(self, v: float | None) -> bool:
        if v is None:
            # An unknown value only passes when we have no floor to check it against.
            return self.min is None
        if self.min is not None and v < self.min:
            return False
        return not (self.max is not None and v > self.max)


class Districts(BaseModel):
    """Folded substring matches against the listing's location text."""

    preferred: list[str] = Field(default_factory=list)
    acceptable: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)


class Keywords(BaseModel):
    required_any: list[str] = Field(default_factory=list)
    blocked: list[str] = Field(default_factory=list)
    bonus: dict[str, int] = Field(default_factory=dict)
    penalty: dict[str, int] = Field(default_factory=dict)


class Criteria(BaseModel):
    price_eur: Range = Range()
    area_m2: Range = Range()
    rooms: Range = Range()
    floor: Range = Range()
    max_price_per_m2: float | None = None
    min_images: int = 0
    districts: Districts = Districts()
    keywords: Keywords = Keywords()
    # None = no preference, True = only private owners, False = only agencies.
    owners_only: bool | None = None
    max_age_minutes: int | None = None


class LlmConfig(BaseModel):
    enabled: bool = False
    model: str = "claude-opus-5-5"
    effort: str = "low"
    max_images: int = 4
    # Don't spend tokens on listings the cheap rules already dislike.
    min_rule_score: int = 40
    notes: str = ""


class Scoring(BaseModel):
    alert_threshold: int = 55
    llm: LlmConfig = LlmConfig()


class DiscordConfig(BaseModel):
    webhook_url: str = ""
    # Ping only for the genuinely good ones, or the channel gets muted.
    mention: str = ""
    mention_min_score: int = 85


class Config(BaseModel):
    poll_interval_seconds: int = 60
    request_timeout_seconds: int = 25
    max_detail_fetches_per_cycle: int = 25
    fingerprint_ttl_hours: int = 72
    database_path: str = "data/stanradar.db"
    sources: list[str] = Field(default_factory=lambda: ["fourzida"])
    criteria: Criteria = Criteria()
    scoring: Scoring = Scoring()
    discord: DiscordConfig = DiscordConfig()

    @classmethod
    def load(cls, path: str | Path) -> Config:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(_expand(raw))


def _expand(obj: Any) -> Any:
    """Replace ${VAR} with the environment value, so secrets stay out of the YAML."""
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v) for v in obj]
    if isinstance(obj, str):
        return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), obj)
    return obj
