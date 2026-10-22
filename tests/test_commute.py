from stanradar.commute import resolve_anchor
from stanradar.config import Commute, Criteria, Range
from stanradar.models import Listing
from stanradar.scoring import evaluate


def table():
    return resolve_anchor("navigator")


def table_estimate(location, t=None):
    return (t or table()).estimate(location)


def test_the_office_block_is_the_closest_thing_to_itself():
    minutes, area = table_estimate("Blok 43, Novi Beograd, Beograd", table())
    assert area == "blok 43"
    assert minutes <= 10


def test_across_the_river_can_beat_parts_of_novi_beograd():
    """The whole reason for a travel-time radius rather than a district list.

    Banovo brdo is across the Sava but closer to Blok 43 than the far west of Novi Beograd.
    """
    banovo, _ = table_estimate("Banovo brdo, Cukarica, Beograd", table())
    ledine, _ = table_estimate("Ledine, Novi Beograd, Beograd", table())
    assert banovo < ledine


def test_the_most_specific_match_wins_not_the_lowest():
    """The catch-all must not override a specific sub-area.

    `novi beograd` is 18 minutes, but Ledine inside it is 24. Taking the lowest match
    made the far west of the municipality look closer than it is.
    """
    assert table_estimate("Blok 45, Novi Beograd, Beograd", table()) == (12, "blok 45")
    assert table_estimate("Ledine, Novi Beograd, Beograd", table()) == (24, "ledine")


def test_substring_block_numbers_are_not_confused():
    # "blok 1" is a substring of "blok 19"; the longest match must win.
    assert table_estimate("Blok 19, Novi Beograd", table())[1] == "blok 19"
    assert table_estimate("Blok 1, Novi Beograd", table())[1] == "blok 1"


def test_diacritics_and_cyrillic_still_match():
    assert table_estimate("Žarkovo, Čukarica", table())[0] is not None
    assert table_estimate("Нови Београд", table())[0] is not None


def test_an_unrecognised_area_is_not_an_error():
    assert table_estimate("Nonexistent Neighbourhood", table()) == (None, None)
    assert table_estimate("", table()) == (None, None)


def test_config_overrides_are_merged_over_the_defaults():
    merged = resolve_anchor("navigator", {"Žarkovo": 5, "brand new area": 7})
    assert merged.areas["zarkovo"] == 5  # overridden, and folded
    assert merged.areas["brand new area"] == 7  # added
    assert merged.areas["blok 43"] == table().areas["blok 43"]  # untouched


def test_an_override_beats_the_municipality_fallback():
    """A corrected neighbourhood must win over the coarse municipality average."""
    merged = resolve_anchor("navigator", {"neko naselje": 9})
    assert merged.estimate("Neko Naselje, Zvezdara, Beograd") == (9, "neko naselje")


def test_a_municipality_fallback_is_used_only_when_no_area_matches():
    t = table()
    # Unlisted sub-area -> municipality average.
    assert t.estimate("Nepoznato Naselje, Opština Novi Beograd")[1] == "novi beograd"
    # Listed sub-area -> the specific estimate, not the average.
    assert t.estimate("Ledine, Opština Novi Beograd")[1] == "ledine"


def test_an_unknown_anchor_fails_loudly():
    try:
        resolve_anchor("nowhere")
    except ValueError as exc:
        assert "unknown commute anchor" in str(exc)
    else:
        raise AssertionError("expected ValueError")


# --- integration with the rule filters ----------------------------------


def criteria(max_minutes=20, unknown="include") -> Criteria:
    return Criteria(
        price_eur=Range(min=250, max=400),
        area_m2=Range(min=30),
        rooms=Range(min=0.5),
        commute=Commute(anchor="navigator", max_minutes=max_minutes, unknown=unknown),
    )


def listing(**overrides) -> Listing:
    base = {
        "source": "test",
        "external_id": "1",
        "url": "https://example.com/1",
        "price_eur": 380,
        "area_m2": 45.0,
        "rooms": 1.0,
        "city": "Beograd",
        "municipality": "Novi Beograd",
        "district": "Blok 63",
    }
    base.update(overrides)
    return Listing(**base)


def test_a_flat_inside_the_radius_passes():
    assert evaluate(listing(), criteria()).passed


def test_a_flat_beyond_the_radius_is_rejected_with_the_estimate():
    far = listing(municipality="Voždovac", district="Dušanovac")
    result = evaluate(far, criteria())
    assert not result.passed
    assert "min commute" in result.rejected_for


def test_a_shorter_commute_scores_higher():
    near = evaluate(listing(district="Blok 43"), criteria()).score
    edge = evaluate(listing(district="Zemun", municipality="Zemun"), criteria(max_minutes=30)).score
    assert near > edge


def test_an_unknown_area_is_kept_by_default():
    """At a tight budget, missing a flat costs more than one alert you dismiss."""
    odd = listing(district="Neka Nova Naselja", municipality="Neznano")
    assert evaluate(odd, criteria(unknown="include")).passed


def test_an_unknown_area_can_be_excluded_on_request():
    odd = listing(district="Neka Nova Naselja", municipality="Neznano")
    result = evaluate(odd, criteria(unknown="exclude"))
    assert not result.passed
    assert "unknown area" in result.rejected_for


def test_no_max_minutes_disables_the_filter_entirely():
    far = listing(municipality="Mirijevo", district="Mirijevo")
    assert evaluate(far, criteria(max_minutes=None)).passed
