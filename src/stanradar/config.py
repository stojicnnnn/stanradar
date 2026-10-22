"""Typed config loaded from config.yaml, with ${ENV_VAR} expansion."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

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


class Commute(BaseModel):
    """Travel-time radius around a workplace, instead of a hand-written district list."""

    anchor: str = "navigator"
    max_minutes: int | None = None
    # Merged over the built-in table in commute.py, so you can correct an estimate
    # without touching code: {"zarkovo": 18}
    areas: dict[str, int] = Field(default_factory=dict)
    # What to do with an area the table does not recognise. Portals invent neighbourhood
    # names constantly, so "include" is the safe default - you would rather check a map
    # than miss a flat.
    unknown: Literal["include", "exclude"] = "include"


class Criteria(BaseModel):
    # Which kinds of property to consider. Houses come from a different endpoint or URL on
    # every portal, so asking for them changes what gets fetched, not just what is kept.
    property_types: list[Literal["apartment", "house"]] = Field(
        default_factory=lambda: ["apartment"]
    )
    price_eur: Range = Range()
    area_m2: Range = Range()
    rooms: Range = Range()
    # Bedrooms, NOT rooms. In Serbian listings "soba" counts the living room, so a
    # trosoban (3 rooms) has 2 bedrooms and a cetvorosoban (4) has 3. Verified against
    # nekretnine.rs, the one portal that publishes both numbers. Filtering on `rooms`
    # when you mean bedrooms silently returns flats one bedroom short.
    bedrooms: Range = Range()
    floor: Range = Range()
    max_price_per_m2: float | None = None
    min_images: int = 0
    districts: Districts = Districts()
    commute: Commute = Commute()
    keywords: Keywords = Keywords()
    # None = no preference, True = only private owners, False = only agencies.
    owners_only: bool | None = None
    # "any" | "furnished" | "unfurnished". Listings that do not state it are always kept,
    # because most do not say and dropping them would cost more than it saves.
    furnished: Literal["any", "furnished", "unfurnished"] = "any"
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


class Profile(BaseModel):
    """One named search. Several can run against the same fetched listings."""

    name: str
    criteria: Criteria = Criteria()
    scoring: Scoring = Scoring()
    discord: DiscordConfig = DiscordConfig()


class Config(BaseModel):
    poll_interval_seconds: int = 60
    request_timeout_seconds: int = 25
    max_detail_fetches_per_cycle: int = 25
    fingerprint_ttl_hours: int = 72
    database_path: str = "data/stanradar.db"
    sources: list[str] = Field(default_factory=lambda: ["fourzida"])
    # Top-level criteria/scoring/discord are the DEFAULTS every profile inherits. A profile
    # overrides them field by field, so a second search does not have to repeat the whole
    # keyword and district configuration.
    criteria: Criteria = Criteria()
    scoring: Scoring = Scoring()
    discord: DiscordConfig = DiscordConfig()
    profiles: list[Profile] = Field(default_factory=list)

    @property
    def property_types(self) -> set[str]:
        """Union across profiles - what actually has to be fetched."""
        types: set[str] = set()
        for profile in self.profiles:
            types.update(profile.criteria.property_types)
        return types or {"apartment"}

    @classmethod
    def load(cls, path: str | Path) -> Config:
        raw = _expand(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})

        # Build each profile by layering its overrides onto the top-level defaults.
        defaults = {key: raw.get(key, {}) for key in ("criteria", "scoring", "discord")}
        profiles = []
        for entry in raw.pop("profiles", None) or [{"name": "default"}]:
            merged = {k: _merge(defaults.get(k, {}), entry.get(k, {})) for k in defaults}
            merged["name"] = entry.get("name", "default")
            profiles.append(merged)
        raw["profiles"] = profiles
        return cls.model_validate(raw)


def _merge(base: Any, override: Any) -> Any:
    """Recursive dict merge; `override` wins. Lists replace rather than concatenate.

    Replacing is the right default for a list of districts or keywords: a profile that
    names its own excluded areas means *those*, not those plus the inherited ones.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            out[key] = _merge(out[key], value) if key in out else value
        return out
    return override if override != {} else base


def _expand(obj: Any) -> Any:
    """Replace ${VAR} with the environment value, so secrets stay out of the YAML."""
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v) for v in obj]
    if isinstance(obj, str):
        return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), obj)
    return obj
