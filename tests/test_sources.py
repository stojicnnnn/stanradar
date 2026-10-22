"""Adapter parsing tests.

The fixtures are trimmed copies of what each portal actually returned in 2026-10, with the
field shapes left exactly as they came off the wire - including 4zida's resizer URL
templates, halooglasi's legend-inside-value markup, and nekretnine's Italian field names.
"""

from stanradar.sources.fourzida import FourZida
from stanradar.sources.halooglasi import HaloOglasi
from stanradar.sources.kupujemprodajem import KupujemProdajem
from stanradar.sources.nekretnine import Nekretnine
from stanradar.sources.oglasi import Oglasi

# --- 4zida ---------------------------------------------------------------

FOURZIDA_AD = {
    "id": "6ad9efa0e74ed4a812016f6d",
    "category": "flat",
    "for": "rent",
    "m2": 65,
    "price": 900,
    "roomCount": 2.5,
    "redactedFloor": 4,
    "redactedTotalFloors": 6,
    "createdAt": "2026-10-22T11:12:32+00:00",
    "address": "Cara Dušana",
    "safeAddress": "Cara Dušana",
    "detailedTitle": "Dvoiposoban stan za izdavanje, Cara Dušana, 900€, 65m²",
    "description100": "Dvoiposoban stan u zgradi za izdavanje, Zemunske kapije, 65 m2.",
    "placeNames": ["Zemunske kapije", "Zemun opština", "Beograd"],
    "urlPath": "/izdavanje-stanova/zemunske-kapije/dvoiposoban-stan/6ad9efa0e74ed4a812016f6d",
    "agencyUrl": "https://www.4zida.rs/agencije/dom-invest-nekretnine/37311",
    "imageCount": 9,
    "image": {
        "search": {
            "380x0_fill_0_webp": "https://resizer2.4zida.rs/x/rs:fill:380:0:0/abc.webp",
            "380x0_fill_0_jpeg": "https://resizer2.4zida.rs/x/rs:fill:380:0:0/abc.jpeg",
        }
    },
}


def test_fourzida_maps_the_search_payload():
    listing = FourZida(client=None)._to_listing(FOURZIDA_AD)
    assert listing.source == "fourzida"
    assert listing.external_id == "6ad9efa0e74ed4a812016f6d"
    assert listing.url.startswith("https://www.4zida.rs/izdavanje-stanova/")
    assert listing.price_eur == 900
    assert listing.area_m2 == 65
    assert listing.rooms == 2.5
    assert (listing.floor, listing.total_floors) == (4, 6)
    # placeNames runs fine -> coarse, the opposite of halooglasi.
    assert listing.district == "Zemunske kapije"
    assert listing.municipality == "Zemun opština"
    assert listing.city == "Beograd"
    assert listing.price_per_m2 == 13.85
    assert listing.is_agency is True
    assert listing.posted_at is not None


def test_fourzida_prefers_the_jpeg_variant():
    listing = FourZida(client=None)._to_listing(FOURZIDA_AD)
    assert listing.images == ["https://resizer2.4zida.rs/x/rs:fill:380:0:0/abc.jpeg"]


def test_fourzida_survives_a_listing_with_no_photo_or_agency():
    sparse = {**FOURZIDA_AD, "image": None, "agencyUrl": None}
    listing = FourZida(client=None)._to_listing(sparse)
    assert listing.images == []
    assert listing.is_agency is False


# --- halooglasi ----------------------------------------------------------

HALO_HTML = """
<div id="ad-list-2" class="row product-list">
  <div class="product-item product-list-item Premium real-estates" data-id="5425647861898">
    <div class="central-feature"><span data-value="1.550"><i>1.550&nbsp;&euro;</i></span></div>
    <figure class="pi-img-wrapper">
      <a class="a-images" href="/nekretnine/izdavanje-stanova/vracar-lux/5425647861898">
        <img src="https://img.halooglasi.com//slike/oglasi/Thumbs/261022/m/vracar-lux.jpg">
      </a>
      <div class="pi-img-count-wrapper"><span class="pi-img-count-num">13</span></div>
    </figure>
    <div class="pi-img-wrapper-under">
      <span class="publish-date">22.10.2026.</span>
      <span class="basic-info">
        <span data-field-name='oglasivac_nekretnine_s' data-field-value='agencija'>Agencija</span>
      </span>
    </div>
    <h3 class="product-title">
      <a href="/nekretnine/izdavanje-stanova/vracar-lux/5425647861898">Vra&#269;ar - Luksuzan</a>
    </h3>
    <ul class="subtitle-places"><li>Beograd</li><li>Op&#353;tina Vra&#269;ar</li><li>Crveni krst</li></ul>
    <ul class="product-features">
      <li><div class='value-wrapper'>85&nbsp;m<sup>2</sup><span class='legend'>Kvadratura</span></div></li>
      <li><div class='value-wrapper'>3.0&nbsp;<span class='legend'>Broj soba</span></div></li>
      <li><div class='value-wrapper'>VI/7&nbsp;<span class='legend'>Spratnost</span></div></li>
    </ul>
    <p class="text-description-list product-description short-desc">Izdaje se luksuzan stan.</p>
  </div>
  <div class="product-item product-list-item banner-list" data-id="999">ad slot</div>
</div>
"""


def _halo_items():
    from selectolax.lexbor import LexborHTMLParser

    source = HaloOglasi(client=None)
    tree = LexborHTMLParser(HALO_HTML)
    return [
        source._to_listing(node)
        for node in tree.css("div.product-item[data-id]")
        if "banner-list" not in (node.attributes.get("class") or "")
    ]


def test_halooglasi_parses_an_item():
    (listing,) = _halo_items()
    assert listing.external_id == "5425647861898"
    assert listing.price_eur == 1550
    assert listing.area_m2 == 85
    assert listing.rooms == 3.0
    # Roman numerals, which only this portal uses.
    assert (listing.floor, listing.total_floors) == (6, 7)
    # subtitle-places runs coarse -> fine, the opposite of 4zida.
    assert listing.city == "Beograd"
    assert listing.district == "Crveni krst"
    assert listing.is_agency is True
    assert listing.url == "https://www.halooglasi.com/nekretnine/izdavanje-stanova/vracar-lux/5425647861898"


def test_halooglasi_upgrades_the_thumbnail_to_the_full_photo():
    (listing,) = _halo_items()
    assert "/Thumbs/" not in listing.images[0]


def test_halooglasi_skips_banner_slots():
    # The banner reuses the product-item class but is not an advert.
    assert len(_halo_items()) == 1


# --- oglasi.rs -----------------------------------------------------------

OGLASI_HTML = """
<div itemscope itemtype="http://schema.org/ItemList">
<article itemprop="itemListElement" itemscope itemtype="http://schema.org/Product">
  <figure>
    <a class="fpogl-list-image" href="/oglas/03-3991106/jednoiposoban-stan-u-zmaj-jovinoj">
      <img src="https://media.oglasi.rs/abc/medium-stan.jpg" itemprop="image">
    </a>
  </figure>
  <small>DIVIS NEKRETNINE DOO Reg. br. 005</small>
  <small>Objavljen: <time datetime="2026-10-22T13:18:21+02:00">22.10.2026.</time></small>
  <span class="text-price"><strong>750,00&nbsp;EUR</strong></span>
  <a class='fpogl-list-title' href="/oglas/03-3991106/jednoiposoban-stan-u-zmaj-jovinoj" itemprop="url">
    <h2 itemprop="name">Jednoiposoban stan za izdavanje u Zmaj Jovinoj</h2>
  </a>
  <a itemprop="category" href="/nekretnine">Nekretnine</a>
  <a itemprop="category" href="/nekretnine/izdavanje-stanova">Izdavanje stanova</a>
  <a itemprop="category" href="/x">Beograd</a>
  <a itemprop="category" href="/y">Stari Grad</a>
  <a itemprop="category" href="/z">Studentski trg</a>
  <div class="col-sm-6">Sobnost: <strong>Jednoiposoban</strong></div>
  <div class="col-sm-6">Opremljenost: <strong>Name&#353;ten</strong></div>
  <div class="col-sm-6">Kvadratura: <strong>33m2</strong></div>
  <div class="col-sm-6">Grejanje: <strong>Gradsko</strong></div>
  <p itemprop="description">Izdaje se kompletno renoviran stan.</p>
</article>
</div>
"""


def _oglasi_item():
    from selectolax.lexbor import LexborHTMLParser

    tree = LexborHTMLParser(OGLASI_HTML)
    node = tree.css_first("article[itemprop='itemListElement']")
    return Oglasi(client=None)._to_listing(node)


def test_oglasi_parses_an_item():
    listing = _oglasi_item()
    assert listing.external_id == "03-3991106"
    assert listing.price_eur == 750
    assert listing.area_m2 == 33
    assert listing.rooms == 1.5  # "Jednoiposoban"
    assert listing.city == "Beograd"
    assert listing.district == "Studentski trg"
    assert listing.raw["heating"] == "Gradsko"


def test_oglasi_keeps_the_second_precision_timestamp():
    listing = _oglasi_item()
    assert listing.posted_at is not None
    assert listing.posted_at.minute == 18 and listing.posted_at.second == 21


def test_oglasi_reads_the_agency_registration_number():
    listing = _oglasi_item()
    assert listing.is_agency is True
    assert "DIVIS" in listing.advertiser


# --- nekretnine.rs -------------------------------------------------------

NEKRETNINE_RESULT = {
    "seo": {"url": "https://www.nekretnine.rs/oglasi/1603359/", "anchor": "Dvosobni stan"},
    "realEstate": {
        "id": 1603359,
        "title": "Dvosobni stan, Severni bulevar, Beograd",
        "advertiser": {"agency": {"id": 745, "displayName": "KEY REAL ESTATE"}},
        "properties": [
            {
                "caption": "BULBULDER 2.0 - ZVEZDARA",
                "price": {"value": 900, "formattedValue": "€ 900/mesec"},
                "surface": "78 m²",
                "rooms": "2",
                "elevator": True,
                "floor": {"abbreviation": "3", "floorOnlyValue": "3"},
                "location": {
                    "address": "Ulica Urosa Trojanovica",
                    "city": "Beograd",
                    "macrozone": "Zvezdara",
                    "microzone": "Severni bulevar",
                    "latitude": 44.8049,
                    "longitude": 20.4962,
                },
                "ga4Heating": "Autonomo",
                "multimedia": {
                    "photos": [
                        {
                            "id": 1,
                            "urls": {
                                "small": "https://pic.nekretnine.rs/image/1/xxs-c.jpg",
                                "large": "https://pic.nekretnine.rs/image/1/l-c.jpg",
                            },
                        }
                    ]
                },
            }
        ],
    },
}


def test_nekretnine_maps_the_next_data_payload():
    listing = Nekretnine(client=None)._to_listing(NEKRETNINE_RESULT)
    assert listing.external_id == "1603359"
    assert listing.url == "https://www.nekretnine.rs/oglasi/1603359/"
    assert listing.price_eur == 900
    assert listing.area_m2 == 78
    assert listing.rooms == 2.0
    assert listing.floor == 3
    assert listing.district == "Severni bulevar"
    assert listing.municipality == "Zvezdara"
    assert listing.is_agency is True
    assert listing.advertiser == "KEY REAL ESTATE"
    assert listing.raw["elevator"] is True
    # The list payload has no publish date, which is why this source relies on the sort.
    assert listing.posted_at is None


def test_nekretnine_picks_the_largest_photo():
    listing = Nekretnine(client=None)._to_listing(NEKRETNINE_RESULT)
    assert listing.images == ["https://pic.nekretnine.rs/image/1/l-c.jpg"]


def test_nekretnine_skips_a_result_with_no_properties():
    broken = {"seo": {}, "realEstate": {"id": 1, "properties": []}}
    assert Nekretnine(client=None)._to_listing(broken) is None


# --- kupujemprodajem -----------------------------------------------------

KP_AD = {
    "ad_id": 196569675,
    "ad_url": "/nekretnine-izdavanje/stanovi/gandijeva-blok-63-novi-beograd-beograd/oglas/196569675",
    "group_name": "Stanovi",
    "category_name": "Nekretnine | Izdavanje",
    "currency": "eur",
    "price": 400,
    "name_decoded": "Gandijeva, Blok 63, Novi Beograd, Beograd",
    "description_snippet_decoded": "Novi Beograd - Blok 63",
    "location_name": "Beograd | Opština Novi Beograd | Blok 63",
    "posted": "2026-10-22 02:16:39",
    "photo_path1": "/photos/oglasi/5/67/196569675/196569675_abc.webp",
    "show_directly_from_owner": True,
    "is_display_priority": False,
    "view_count": 12,
    "ad_attributes": [
        {
            "code": "realEstateApartmentSummaryLong",
            "attributes": [
                {"code": "realEstateArea", "values": ["49 m²"]},
                {"code": "realEstateApartmentNumberOfRooms", "values": ["1.5"]},
                {"code": "realEstateFloorInfo", "values": ["PR/4"]},
            ],
        }
    ],
}


def test_kupujemprodajem_maps_a_search_result():
    listing = KupujemProdajem(client=None)._to_listing(KP_AD)
    assert listing.external_id == "196569675"
    assert listing.url.startswith("https://www.kupujemprodajem.com/nekretnine-izdavanje/")
    assert listing.price_eur == 400
    assert listing.area_m2 == 49
    assert listing.rooms == 1.5
    # "PR/4" is ground floor of four, the same notation halooglasi uses.
    assert (listing.floor, listing.total_floors) == (0, 4)
    assert listing.municipality == "Opština Novi Beograd"
    assert listing.district == "Blok 63"
    assert listing.is_agency is False  # KP's "directly from owner" badge
    assert listing.images == [
        "https://images.kupujemprodajem.com/photos/oglasi/5/67/196569675/196569675_abc.webp"
    ]


def test_kupujemprodajem_timestamp_is_belgrade_local_not_utc():
    """A fixed offset would be an hour wrong for half the year and skew the listing age."""
    listing = KupujemProdajem(client=None)._to_listing(KP_AD)
    assert listing.posted_at is not None
    assert listing.posted_at.utcoffset().total_seconds() == 2 * 3600  # CEST in October
    assert listing.posted_at.hour == 2


def test_kupujemprodajem_drops_commercial_premises_and_sales():
    """The keyword search mixes in Poslovni prostor and sale listings."""
    commercial = {**KP_AD, "group_name": "Poslovni prostor"}
    for_sale = {**KP_AD, "category_name": "Nekretnine | Prodaja"}
    source = KupujemProdajem(client=None)
    assert source._to_listing(commercial) is None
    assert source._to_listing(for_sale) is None


def test_kupujemprodajem_needs_a_detail_fetch_for_its_photos():
    """The search payload has one thumbnail, so min_images must not be judged on it."""
    assert KupujemProdajem.has_detail_fetch is True


def test_kupujemprodajem_strips_html_from_descriptions():
    from stanradar.sources.kupujemprodajem import _strip_html

    text = _strip_html("<p><b>Blok 63</b></p><p>&nbsp;</p><p>50% zakupnine</p>")
    assert "<" not in text and "&nbsp;" not in text
    assert "Blok 63" in text and "50% zakupnine" in text
