# Belgrade rental sources

Every site below was probed directly in October 2026 — the status column reflects what the
server actually returned, not what the site claims to offer. Run `stanradar sources` to see
the same list from the code.

## Monitored

| Portal | Status | How it is read | Freshness | Notes |
|---|---|---|---|---|
| **[4zida.rs](https://www.4zida.rs/izdavanje-stanova/beograd)** | working | public JSON API, `api.4zida.rs/v6` | **exact, to the second** | The best source by a wide margin. Supports `sort=createdAtDesc`, returns a real `createdAt`, and costs a few KB per poll. Full description and all photos from `/v6/ads/{id}`. |
| **[nekretnine.rs](https://www.nekretnine.rs/izdavanje-stanova/beograd/)** | working | `__NEXT_DATA__` JSON embedded in the page | sort order only | Richest payload: coordinates, lift, heating, garage, full-resolution photos, agency vs owner. No publish timestamp in the list payload. Needs browser-TLS. |
| **[halooglasi.com](https://www.halooglasi.com/nekretnine/izdavanje-stanova/beograd)** | working | server-rendered HTML | day precision | Largest general classifieds. Newest-first sort reachable by its sort GUID, returning a ~35 KB fragment of 20 full items. Needs browser-TLS. |
| **[oglasi.rs](https://www.oglasi.rs/nekretnine/izdavanje-stanova/beograd)** | working | schema.org microdata | **exact, to the second** | Clean markup, newest-first by default, full ISO timestamps. Also exposes furnishing and heating as structured fields. |
| **[kupujemprodajem.com](https://www.kupujemprodajem.com/pretraga?keywords=izdavanje+stana+beograd)** | working | `__NEXT_DATA__` JSON on the **search** page | **exact, to the second** | The big owner-to-owner marketplace, and the best source of non-agency listings and of flats at the bottom of the price range. Read via `/pretraga`, not `/api/` - see below. |

Between them, 4zida and nekretnine.rs carry roughly 3,500 Belgrade rental listings each, with
heavy overlap; halooglasi and oglasi.rs add agency stock and some owner listings the other
two miss. 4zida and oglasi.rs are the two that let you measure true latency, because they are
the only ones that say when a listing was posted.

## Monitored but not yet implemented

Enabling one of these in `config.yaml` fails loudly at startup rather than silently watching
nothing. What is missing is implementation, not investigation:

| Portal | Why it is not done | What it needs |
|---|---|---|
| **[cityexpert.rs](https://cityexpert.rs/izdavanje/stanovi/beograd)** | Single-page app; the category URL returns a ~4 KB shell with no listings. There is a JSON search API at `POST /api/Search/`, but the previously documented `{ptId, cityId, rentOrSale, …}` body now returns 400. | Read the current request shape off the site's own JS bundle or its network traffic. Worth doing: CityExpert manages its own stock, so its listings are verified and rarely cross-posted. |

## Considered and rejected

| Portal | Why not |
|---|---|
| **sasomange.rs** | Gone. The domain returns `410 Gone`; the service appears to have been shut down. |
| **imovina.net** | Redirects away and carries little Belgrade rental stock. |
| **gohome.rs, mojanekretnina.rs, estitor.com, roommateor.com, izdavanje.org** | Aggregators. They re-publish the same listings already covered above, usually with a delay, which is the opposite of what this tool is for. Monitoring them would mostly generate duplicate alerts. |
| **Facebook groups** | Genuinely fast and full of owner listings, but there is no legitimate automated path: the Graph API has not exposed group posts to non-admin third-party apps since 2020, and scraping the logged-in feed breaks Facebook's terms and risks the account doing it. A fresh account makes this *worse*, not better - new account plus automation is the exact pattern their anti-automation systems flag. The practical substitute is per-group push notifications on your phone, which are free, instant and allowed. Much of this supply also cross-posts to kupujemprodajem, which is covered above. |

## Houses

Houses live behind a different endpoint or URL on every portal, so asking for them changes
what gets fetched, not just what is kept:

| Portal | Houses | How |
|---|---|---|
| 4zida.rs | yes, ~450 in Belgrade | `GET /v6/search/houses`, identical response shape |
| oglasi.rs | yes | `/nekretnine/izdavanje-kuca/beograd` |
| kupujemprodajem.com | yes | a second keyword search; results come back in the `Kuće` group |
| halooglasi.com | yes | `/nekretnine/izdavanje-kuca/beograd` (unused while IP-blocked) |
| **nekretnine.rs** | **no** | see below |

**nekretnine.rs serves apartments only.** Every guessable house-rental URL 404s
(`/izdavanje-kuca/beograd/`, `/iznajmljivanje-kuca/…`, and four other patterns), and the one
path that does resolve — `/stambeni-objekti/kuce/grad/beograd/izdavanje/` — silently returns
houses **for sale** (`idContratto=1`), the same trap as the apartment URL. Its own payload
contains no link to a house-rental page either. Rather than risk quietly mixing sale
listings into a rental search, this source declines houses outright; the other three cover
them.

## Things worth knowing about these sites

**Two of them fingerprint TLS.** halooglasi.com and nekretnine.rs return `403` to any request
from Python's `ssl` module, no matter what headers are sent, while `curl` with the same
headers gets `200`. The WAF is inspecting the TLS handshake, not the request. These sources
use `curl_cffi` with `impersonate="chrome"` instead.

Related trap: you must *not* override the impersonated headers. Passing a custom
`User-Agent`/`Accept` set alongside `impersonate="chrome"` makes the headers disagree with
the TLS fingerprint, which is exactly the mismatch these WAFs look for — nekretnine.rs
returns `200` with no custom headers and `403` with them.

**nekretnine.rs has a URL trap.** `/izdavanje-stanova/beograd/` is the rental listing page
(`idContratto=2`, ~3,500 listings). The more plausible-looking
`/stambeni-objekti/stanovi/grad/beograd/izdavanje/…` silently redirects to the **sale**
listings (~9,000) with no error. Verified working sort: `?criterio=data&ordine=desc`, which
returns IDs in a tight newest band; `ordine=asc` returns the oldest.

**kupujemprodajem renders listings only on its search page.** Its category pages
(`/nekretnine-izdavanje/stanovi`) are client-rendered and arrive with no listings in the
HTML; the data comes from `/api/`, which its own `robots.txt` disallows, so the adapter does
not touch it. `/pretraga?keywords=…` is *not* disallowed and does server-render results into
`__NEXT_DATA__`, so that is the path used. Paid-promotion ads are pinned above the newest
ones there, so order cannot be trusted - freshness comes from id-based de-duplication. The
ad page itself also server-renders, which is where the full photo set comes from; the search
payload carries only one thumbnail.

**4zida rejects unknown query parameters with 422.** Only `for`, `placeIds[]`, `priceFrom`,
`priceTo`, `m2From`, `m2To`, `page` and `sort` are known-good. This is why the adapter
pre-filters only broadly and does the precise filtering locally — which is the better design
anyway, since a listing with a mis-tagged `structure` or missing `furnished` field still
reaches you instead of being silently dropped server-side.

**Only two portals state whether a flat is furnished.** This matters because an empty
flat is materially cheaper and means buying everything, so it belongs in the alert rather
than buried in the description. Coverage, measured over a live page from each:

| Portal | Source of truth | Coverage seen |
|---|---|---|
| 4zida.rs | `furnished: "yes"/"no"` on the **detail** payload (absent from search) | every enriched listing |
| oglasi.rs | `Opremljenost` structured field | ~100% (20 furnished, 4 empty of 24) |
| halooglasi.com | free text only | ~35% |
| kupujemprodajem.com | free text only, re-checked against the full description in `enrich()` | ~40% |
| nekretnine.rs | `arredato` in the Italian-named `ga4features` list; absence proves nothing | ~25% |

Everything else falls back to text detection, and a listing that never says is kept rather
than guessed at. One trap worth knowing: **"nenamesten" (unfurnished) contains "namesten"
(furnished) as a substring**, as does "polunamesten", so the negative patterns have to be
tested first - matching the positives first reads every empty flat as furnished.

**The portals disagree about what a studio is.** 4zida encodes a garsonjera as
`roomCount: 0.5`, while halooglasi, oglasi.rs and kupujemprodajem call the same thing 1.0.
A `rooms.min` of 1.0 therefore silently drops every 4zida studio - which, at the bottom of
the price range, is a large share of what is actually available.

**Districts are named inconsistently across portals.** One flat on Uroša Trojanovića is filed
under "Bulbulder" by 4zida and "Severni bulevar" by nekretnine.rs. This is why district
matching is done on folded substrings, and why cross-portal duplicate detection ignores the
district (see `loose_fingerprint` in `models.py`).

## Polite use

These are small companies' websites. The defaults are deliberately gentle: one listing-page
request per portal per minute (~1,440/day each, which is less than a single person browsing),
detail pages fetched only for listings that already passed your filters and capped per cycle,
and a jittered interval so repeated runs do not hit every portal on the same second.

`robots.txt` was checked for each site and the adapters stay within it — notably, oglasi.rs
disallows its query-string search URLs (`/oglasi?q=`, `/oglasi?o=`), so that adapter only ever
requests the canonical category path. Raising the poll rate much above the default would be
both rude and counterproductive, since it risks the 429s and blocks that would cost you the
listings this tool exists to catch.
