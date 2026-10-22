"""Multi-profile config and the bedroom convention."""


import pytest

from stanradar.config import Config
from stanradar.models import Listing
from stanradar.scoring import evaluate

CONFIG = """
database_path: data/t.db
sources: [fourzida]
criteria:
  price_eur: {min: 200, max: 450}
  area_m2: {min: 30}
  keywords:
    blocked: [suteren, na dan]
  districts:
    excluded: [borca, ovca]
scoring:
  alert_threshold: 50
discord:
  webhook_url: "https://example/hook"
profiles:
  - name: solo
  - name: shared-3
    criteria:
      property_types: [apartment, house]
      price_eur: {max: 850}
      bedrooms: {min: 3}
      area_m2: {min: 65}
    scoring:
      alert_threshold: 45
"""


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "c.yaml"
    open(path, "w", encoding="utf-8").write(CONFIG)
    return Config.load(path)


def listing(**overrides) -> Listing:
    base = {
        "source": "t", "external_id": "1", "url": "https://x/1",
        "price_eur": 800, "area_m2": 90.0, "rooms": 4.0,
        "city": "Beograd", "municipality": "Novi Beograd", "district": "Blok 23",
        "images": ["a.jpg", "b.jpg"],
    }
    base.update(overrides)
    return Listing(**base)


def profile(cfg, name):
    return next(p for p in cfg.profiles if p.name == name)


# --- inheritance --------------------------------------------------------


def test_a_profile_inherits_what_it_does_not_override(cfg):
    shared = profile(cfg, "shared-3").criteria
    # Never stated by the profile, so it comes from the defaults.
    assert shared.price_eur.min == 200
    assert shared.keywords.blocked == ["suteren", "na dan"]
    assert shared.districts.excluded == ["borca", "ovca"]
    # Stated by the profile, so it wins.
    assert shared.price_eur.max == 850
    assert shared.area_m2.min == 65


def test_profiles_do_not_leak_into_each_other(cfg):
    assert profile(cfg, "solo").criteria.price_eur.max == 450
    assert profile(cfg, "shared-3").criteria.price_eur.max == 850
    assert profile(cfg, "solo").criteria.property_types == ["apartment"]


def test_discord_and_scoring_are_inherited_too(cfg):
    assert profile(cfg, "shared-3").discord.webhook_url == "https://example/hook"
    assert profile(cfg, "solo").scoring.alert_threshold == 50
    assert profile(cfg, "shared-3").scoring.alert_threshold == 45


def test_a_config_with_no_profiles_still_yields_one(tmp_path):
    path = tmp_path / "c.yaml"
    open(path, "w", encoding="utf-8").write(
        "criteria:\n  price_eur: {max: 500}\ndiscord:\n  webhook_url: x\n"
    )
    cfg = Config.load(path)
    assert [p.name for p in cfg.profiles] == ["default"]
    assert cfg.profiles[0].criteria.price_eur.max == 500


def test_property_types_is_the_union_across_profiles(cfg):
    # Fetching is shared, so the sources must cover what any profile asks for.
    assert cfg.property_types == {"apartment", "house"}


# --- the bedroom convention --------------------------------------------


def test_bedrooms_are_derived_from_rooms_the_serbian_way():
    """"Soba" counts the living room: trosoban = 3 rooms = 2 bedrooms."""
    assert listing(rooms=3.0).bedroom_count() == 2.0   # trosoban
    assert listing(rooms=4.0).bedroom_count() == 3.0   # cetvorosoban
    assert listing(rooms=2.0).bedroom_count() == 1.0   # dvosoban
    assert listing(rooms=1.0).bedroom_count() == 0.0   # jednosoban, no separate bedroom
    assert listing(rooms=0.5).bedroom_count() == 0.0   # garsonjera, floors at zero


def test_a_stated_bedroom_count_beats_the_derived_one():
    # nekretnine.rs publishes this directly; trust it over the arithmetic.
    assert listing(rooms=4.0, bedrooms=2.0).bedroom_count() == 2.0


def test_unknown_rooms_give_unknown_bedrooms():
    assert listing(rooms=None).bedroom_count() is None


def test_a_trosoban_does_not_satisfy_three_bedrooms(cfg):
    """The mistake this profile exists to avoid.

    A 3-room flat has 2 bedrooms. Filtering on `rooms: 3` would have accepted it.
    """
    shared = profile(cfg, "shared-3").criteria
    result = evaluate(listing(rooms=3.0, area_m2=75.0), shared)
    assert not result.passed
    assert result.rejected_for == "2 bedrooms"
    assert evaluate(listing(rooms=4.0), shared).passed


# --- property types -----------------------------------------------------


def test_the_solo_profile_rejects_houses(cfg):
    # Priced inside the solo budget, so the rejection is about the type and nothing else.
    affordable_house = listing(property_type="house", price_eur=400, area_m2=60.0, rooms=2.0)
    result = evaluate(affordable_house, profile(cfg, "solo").criteria)
    assert not result.passed
    assert result.rejected_for == "is a house"


def test_the_shared_profile_accepts_both(cfg):
    shared = profile(cfg, "shared-3").criteria
    assert evaluate(listing(property_type="house"), shared).passed
    assert evaluate(listing(property_type="apartment"), shared).passed
