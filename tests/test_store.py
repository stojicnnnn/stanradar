from stanradar.models import Listing
from stanradar.store import Store


def make(source="fourzida", ext="1", price=700, area=60.0, rooms=2.0, district="Vračar"):
    return Listing(
        source=source,
        external_id=ext,
        url=f"https://{source}.example/{ext}",
        price_eur=price,
        area_m2=area,
        rooms=rooms,
        district=district,
    )


def store(tmp_path) -> Store:
    return Store(str(tmp_path / "t.db"))


def test_a_listing_is_known_only_after_it_is_recorded(tmp_path):
    s = store(tmp_path)
    listing = make()
    assert not s.is_known(listing)
    s.record(listing)
    assert s.is_known(listing)


def test_recording_twice_does_not_raise_or_duplicate(tmp_path):
    s = store(tmp_path)
    listing = make()
    s.record(listing, rule_score=60)
    s.record(listing, rule_score=80)
    assert s.stats()["total"] == 1


def test_notified_at_is_never_cleared_by_a_later_update(tmp_path):
    s = store(tmp_path)
    listing = make()
    s.record(listing, notified=True)
    s.record(listing, rule_score=90)  # notified not passed this time
    assert s.stats()["notified"] == 1


def test_the_same_flat_on_another_portal_is_detected_as_a_duplicate(tmp_path):
    s = store(tmp_path)
    s.record(make(source="fourzida", ext="aaa"))
    cross_posted = make(source="halooglasi", ext="bbb")
    assert s.duplicate_of(cross_posted) == "https://fourzida.example/aaa"


def test_a_genuinely_different_flat_is_not_a_duplicate(tmp_path):
    s = store(tmp_path)
    s.record(make(source="fourzida", ext="aaa", price=700))
    assert s.duplicate_of(make(source="halooglasi", ext="bbb", price=850)) is None


def test_a_listing_is_not_its_own_duplicate(tmp_path):
    s = store(tmp_path)
    listing = make()
    s.record(listing)
    assert s.duplicate_of(listing) is None


def test_priming_is_recorded_per_source(tmp_path):
    s = store(tmp_path)
    assert not s.is_primed("fourzida")
    s.mark_primed("fourzida")
    assert s.is_primed("fourzida")
    assert not s.is_primed("oglasi")


def test_failures_accumulate_and_a_success_resets_them(tmp_path):
    s = store(tmp_path)
    assert s.mark_result("oglasi", "boom") == 1
    assert s.mark_result("oglasi", "boom") == 2
    assert s.mark_result("oglasi", "boom") == 3
    assert s.mark_result("oglasi", None) == 0
    assert s.mark_result("oglasi", "boom") == 1


def test_cross_portal_match_is_found_despite_different_district_names(tmp_path):
    """The case this exists for: one flat on Uroša Trojanovića, filed as "Bulbulder" by
    4zida and "Severni bulevar" by nekretnine.rs. The strong fingerprint cannot match it.
    """
    s = store(tmp_path)
    s.record(make(source="fourzida", ext="a", price=900, area=78.0, district="Bulbulder"))
    other = make(source="nekretnine", ext="b", price=900, area=78.0, district="Severni bulevar")
    assert s.duplicate_of(other) is None  # too different to suppress on
    assert s.similar_on_another_portal(other) == "https://fourzida.example/a"


def test_a_loose_match_on_the_same_portal_is_ignored(tmp_path):
    # Same-portal repeats are already handled by the strong fingerprint.
    s = store(tmp_path)
    s.record(make(source="fourzida", ext="a", district="Bulbulder"))
    same_portal = make(source="fourzida", ext="b", district="Neimar")
    assert s.similar_on_another_portal(same_portal) is None


def test_a_loose_match_needs_price_area_and_rooms(tmp_path):
    s = store(tmp_path)
    s.record(make(source="fourzida", ext="a", price=900, area=78.0, rooms=2.0))
    incomplete = make(source="oglasi", ext="b", price=900, area=78.0, rooms=None)
    assert incomplete.loose_fingerprint() is None
    assert s.similar_on_another_portal(incomplete) is None
