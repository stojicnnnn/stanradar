from stanradar.text import (
    detect_furnished,
    first_number,
    fold,
    parse_floor,
    parse_price_eur,
    parse_rooms,
)


def test_fold_handles_latin_diacritics_and_cyrillic():
    # The same neighbourhood is written all three ways across the portals.
    assert fold("Vračar") == "vracar"
    assert fold("Vracar") == "vracar"
    assert fold("Врачар") == "vracar"
    assert fold("ВРАЧАР") == "vracar"
    assert fold("Нови Београд") == "novi beograd"
    assert fold("Žarkovo  Đeram ŠUMICE") == "zarkovo djeram sumice"


def test_fold_is_none_safe():
    assert fold(None) == ""
    assert fold("") == ""


def test_first_number_distinguishes_thousands_from_decimals():
    assert first_number("1.250") == 1250.0  # thousands separator
    assert first_number("3.0") == 3.0  # decimal point
    assert first_number("2,5") == 2.5  # Serbian decimal comma
    assert first_number("78 m²") == 78.0
    assert first_number("nema") is None


def test_parse_price_rejects_implausible_values():
    assert parse_price_eur("1.550 €") == 1550
    assert parse_price_eur("750,00 EUR") == 750
    assert parse_price_eur("0631234567") is None  # a phone number, not a price
    assert parse_price_eur("") is None


def test_parse_rooms_from_serbian_words():
    assert parse_rooms("Garsonjera") == 1.0
    assert parse_rooms("Jednoiposoban") == 1.5
    assert parse_rooms("Dvoiposoban stan") == 2.5
    assert parse_rooms("Četvorosoban") == 4.0
    assert parse_rooms("3.0") == 3.0


def test_parse_floor_handles_roman_numerals_and_basements():
    assert parse_floor("VI/7") == (6, 7)
    assert parse_floor("IX/10") == (9, 10)
    assert parse_floor("3/5") == (3, 5)
    # The distinction that matters: a raised ground floor is not a half-basement.
    assert parse_floor("VPR/4") == (0, 4)
    assert parse_floor("SUT/5") == (-1, 5)
    assert parse_floor("PSUT/2") == (-2, 2)
    assert parse_floor(None) == (None, None)


def test_detect_furnished_reads_the_serbian_words():
    assert detect_furnished("Kompletno namešten stan") is True
    assert detect_furnished("Polunamešten") is True            # some furniture counts
    assert detect_furnished("delimično opremljen") is True
    assert detect_furnished("Prazan stan bez stvari") is False
    assert detect_furnished("izdaje se prazan") is False


def test_unfurnished_is_not_read_as_furnished():
    """The substring trap: "nenamesten" contains "namesten".

    Checking the positive words first would read every unfurnished flat as furnished -
    backwards on one of the biggest cost differences in a listing.
    """
    for text in ["Nenamešten stan", "ne namešten", "nije namešten", "NENAMEŠTENO"]:
        assert detect_furnished(text) is False, text


def test_silence_about_furnishing_is_not_a_guess():
    # Most listings never mention it; None is the honest answer.
    assert detect_furnished("Lep stan na Vračaru, 3. sprat") is None
    assert detect_furnished(None) is None
    assert detect_furnished("") is None


def test_detect_furnished_searches_all_given_texts():
    assert detect_furnished("Dvosoban stan", "Stan je nenamešten.") is False
