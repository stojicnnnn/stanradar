from stanradar.config import Criteria, Districts, Keywords, Range
from stanradar.models import Listing
from stanradar.scoring import evaluate


def criteria(**overrides) -> Criteria:
    base = {
        "price_eur": Range(min=400, max=900),
        "area_m2": Range(min=35, max=90),
        "rooms": Range(min=1.5),
        "floor": Range(min=0),
        "max_price_per_m2": 20,
        "min_images": 2,
        "districts": Districts(
            preferred=["vracar", "stari grad"],
            acceptable=["zvezdara"],
            excluded=["borca", "ovca"],
        ),
        "keywords": Keywords(
            blocked=["na dan", "suteren"],
            bonus={"renoviran": 8, "lift": 5},
            penalty={"bez lifta": 5},
        ),
    }
    base.update(overrides)
    return Criteria(**base)


def listing(**overrides) -> Listing:
    base = {
        "source": "test",
        "external_id": "1",
        "url": "https://example.com/1",
        "title": "Dvosoban stan",
        "description": "Lep stan, renoviran, zgrada ima lift.",
        "price_eur": 700,
        "area_m2": 60.0,
        "rooms": 2.0,
        "floor": 3,
        "total_floors": 5,
        "city": "Beograd",
        "municipality": "Vračar",
        "district": "Kalenić",
        "images": ["a.jpg", "b.jpg", "c.jpg"],
    }
    base.update(overrides)
    return Listing(**base)


def test_a_good_listing_passes_and_scores_well():
    result = evaluate(listing(), criteria())
    assert result.passed
    assert result.score > 70
    # The reason names the pattern that matched, not the listing's own sub-district.
    assert "preferred area: vracar" in result.reasons


def test_hard_filters_reject_with_a_reason():
    cases = {
        "price": listing(price_eur=1500),
        "area": listing(area_m2=25.0),
        "rooms": listing(rooms=1.0),
        "floor": listing(floor=-1),
        "EUR/m2": listing(price_eur=900, area_m2=40.0),
    }
    for expected, candidate in cases.items():
        result = evaluate(candidate, criteria())
        assert not result.passed, f"{expected} should have been rejected"
        assert expected.split()[0] in result.rejected_for


def test_excluded_district_is_rejected_even_when_cheap():
    result = evaluate(
        listing(price_eur=400, municipality="Borča", district="Borča"), criteria()
    )
    assert not result.passed
    assert "excluded area" in result.rejected_for


def test_blocked_keyword_is_rejected_regardless_of_diacritics():
    # "Izdaje se NA DAN" written any way should still be caught.
    result = evaluate(listing(description="Izdavanje NA DAN, turisticki"), criteria())
    assert not result.passed
    assert "blocked keyword" in result.rejected_for


def test_photo_count_is_deferred_until_detail_is_fetched():
    """The bug this guards: 4zida's search response carries one thumbnail.

    Checking min_images on the listing-page pass rejected every 4zida listing before its
    nine real photos were ever fetched.
    """
    thin = listing(images=["only-thumbnail.jpg"])
    assert evaluate(thin, criteria(), full_detail=False).passed
    assert not evaluate(thin, criteria(), full_detail=True).passed


def test_required_keywords_are_also_deferred():
    crit = criteria(keywords=Keywords(required_any=["terasa"]))
    bare = listing(description="")
    assert evaluate(bare, crit, full_detail=False).passed
    assert not evaluate(bare, crit, full_detail=True).passed


def test_cheaper_and_bigger_scores_higher():
    cheap = evaluate(listing(price_eur=450), criteria()).score
    dear = evaluate(listing(price_eur=890), criteria()).score
    assert cheap > dear


def test_preferred_district_beats_acceptable_one():
    preferred = evaluate(listing(municipality="Vračar", district="Neimar"), criteria())
    acceptable = evaluate(listing(municipality="Zvezdara", district="Zvezdara"), criteria())
    assert preferred.score > acceptable.score


def test_owner_listings_are_preferred_when_no_filter_is_set():
    # Priced near the top of the band so neither result sits against the 100 ceiling.
    owner = evaluate(listing(price_eur=880, is_agency=False), criteria()).score
    agency = evaluate(listing(price_eur=880, is_agency=True), criteria()).score
    assert owner > agency


def test_a_solid_listing_leaves_headroom_above_it():
    """A solid-but-ordinary listing must not already be at the ceiling.

    The earlier weighting put this exact listing at 100, which made every decent flat
    indistinguishable from a great one and defeated the point of `alert_threshold`.
    Only a listing that is exceptional on every axis should approach 100.
    """
    solid = evaluate(listing(), criteria()).score
    exceptional = evaluate(
        listing(
            price_eur=420,
            area_m2=88.0,
            description="Potpuno renoviran, lift, terasa, parking, klima",
            images=[f"{i}.jpg" for i in range(10)],
            is_agency=False,
        ),
        criteria(),
    ).score
    assert 70 <= solid < 90
    assert exceptional - solid >= 10


def test_keyword_padding_cannot_dominate_the_score():
    crit = criteria(keywords=Keywords(bonus={f"w{i}": 10 for i in range(8)}))
    padded = listing(description=" ".join(f"w{i}" for i in range(8)))
    plain = listing(description="")
    assert evaluate(padded, crit).score - evaluate(plain, crit).score <= 12


def test_owners_only_filter_rejects_agencies():
    result = evaluate(listing(is_agency=True), criteria(owners_only=True))
    assert not result.passed
    assert result.rejected_for == "agency listing"


def test_unknown_price_passes_only_without_a_floor():
    assert not evaluate(listing(price_eur=None), criteria()).passed
    assert evaluate(listing(price_eur=None), criteria(price_eur=Range())).passed


def test_furnished_filter_is_off_by_default():
    assert evaluate(listing(furnished=False), criteria()).passed
    assert evaluate(listing(furnished=True), criteria()).passed


def test_an_empty_flat_is_not_penalised_against_a_furnished_one():
    """Empty flats are an accepted option, so furnishing must not move the score.

    The earlier default config gave bonuses for a washing machine and dishwasher, which
    only a furnished flat can earn, so furnished quietly outranked empty.
    """
    empty = evaluate(listing(furnished=False), criteria()).score
    furnished = evaluate(listing(furnished=True), criteria()).score
    assert empty == furnished


def test_the_furnished_filter_can_be_narrowed_when_asked():
    only_empty = criteria(furnished="unfurnished")
    assert evaluate(listing(furnished=False), only_empty).passed
    rejected = evaluate(listing(furnished=True), only_empty)
    assert not rejected.passed
    assert rejected.rejected_for == "furnished"


def test_listings_that_do_not_state_furnishing_are_always_kept():
    """Most portals never say, so dropping silence would cost far more than it saves."""
    for want in ("furnished", "unfurnished"):
        assert evaluate(listing(furnished=None), criteria(furnished=want)).passed
