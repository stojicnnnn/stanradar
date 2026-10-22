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
| **[kupujemprodajem.com](https://www.kupujemprodajem.com/)** | The biggest owner-to-owner marketplace in Serbia, so the best source of non-agency listings. Its `robots.txt` disallows `/api/`, which rules out the clean JSON path, and the guessed HTML category paths returned 404. | Identify the correct HTML category URL for apartment rentals in Belgrade, then parse it. |

## Considered and rejected

| Portal | Why not |
|---|---|
| **sasomange.rs** | Gone. The domain returns `410 Gone`; the service appears to have been shut down. |
| **imovina.net** | Redirects away and carries little Belgrade rental stock. |
| **gohome.rs, mojanekretnina.rs, estitor.com, roommateor.com, izdavanje.org** | Aggregators. They re-publish the same listings already covered above, usually with a delay, which is the opposite of what this tool is for. Monitoring them would mostly generate duplicate alerts. |
| **Facebook groups** | Genuinely fast and full of owner listings, but automated access violates the platform's terms and the Graph API does not expose group posts to third-party apps. Worth watching by hand. |

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

**4zida rejects unknown query parameters with 422.** Only `for`, `placeIds[]`, `priceFrom`,
`priceTo`, `m2From`, `m2To`, `page` and `sort` are known-good. This is why the adapter
pre-filters only broadly and does the precise filtering locally — which is the better design
anyway, since a listing with a mis-tagged `structure` or missing `furnished` field still
reaches you instead of being silently dropped server-side.

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
