# stanradar

Watches Belgrade rental portals around the clock and pings Discord the moment an apartment
that actually fits you is posted — usually within a minute of it going live.

Good flats in Belgrade are gone in the first hour or two. This exists to make sure you are
one of the first calls, not the fortieth.

## What it does

```
poll portals -> drop what you'd never take -> fetch the details -> read the photos
             -> score the fit -> alert on Discord
```

Five portals are polled every 60 seconds (see [docs/SOURCES.md](docs/SOURCES.md) for the
full list and what was found when each was probed). Each new listing goes through:

1. **Hard filters** — price, size, rooms, floor, €/m², commute time, deal-breaker keywords.
   Cheap and instant, so a listing that is out of budget costs nothing.
2. **Detail fetch** — only for listings that got past step 1, so detail requests are never
   wasted on a flat you'd never take.
3. **Rule score (0–100)** — how good a deal it is on the numbers: price within your band,
   €/m² against your ceiling, commute time, preferred district, extra space, amenities.
4. **AI fit score** — the photos and the full Serbian description go to Claude, which judges
   what rules cannot: real condition of the kitchen and bathroom, natural light, layout
   problems, and whether the listing is hiding something. Only listings that already look
   promising are sent, so the token spend tracks plausible listings rather than all of them.
5. **Discord alert** — one embed with the price, the thumbnail, the verdict and the link, so
   you can decide from the phone notification.

Along the way it suppresses the same flat re-posted by three agencies, and flags when a flat
looks like it is also listed on another portal.

## Setup

```bash
git clone <this repo> && cd stanradar

uv venv && uv pip install -e ".[dev]"      # or: python -m venv .venv && pip install -e ".[dev]"

cp config.example.yaml config.yaml
cp .env.example .env
```

Put your Discord webhook in `.env` (Discord → Server Settings → Integrations → Webhooks →
New Webhook → Copy URL), and your Anthropic API key if you want the AI scoring:

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
ANTHROPIC_API_KEY=sk-ant-...
```

Then edit `config.yaml`. **The defaults are a starting point, not a recommendation** — at
minimum set `criteria.price_eur`, `criteria.area_m2` and `criteria.districts` to what you
actually want, and put anything the rules cannot express into `scoring.llm.notes`, which goes
straight into the prompt:

```yaml
scoring:
  llm:
    notes: |
      I work from home, so a quiet flat and a room that can hold a desk matter more
      than being close to nightlife. I would rather have a smaller renovated flat
      than a big tired one.
```

## Running it

```bash
stanradar probe            # fetch one page per portal, show how your rules score it
stanradar test-discord     # confirm the webhook works
stanradar watch --dry-run  # full pipeline, matches printed to the console
stanradar watch            # the real thing
```

Start with `probe`. It prints every listing with either its score or the reason it was
rejected, which is the fastest way to find out your filters are too tight:

```
[PASS  89]  1200 EUR   80.0 m2  Neimar, Vračar, Beograd            12 min ago
[skip (price 2400)]  2400 EUR  179.0 m2  Kralja Petra, Stari grad   47 min ago
[skip (floor -1)]     650 EUR   43.0 m2  Milete Jakšića, Zvezdara   29 min ago
```

Other commands:

```bash
stanradar sources          # what is monitored, and what still needs an adapter
stanradar recent -n 30     # what has been seen and what was alerted
stanradar watch --once     # a single cycle, then exit
```

### The first run is quiet on purpose

On the first cycle each portal is *primed*: everything currently listed is recorded as
already-seen without alerting, so starting the app does not dump 300 historical listings into
your channel. You get one short message per portal saying how many current listings match
your criteria, and real alerts begin with the next new post. Run `probe` if you want to see
the current stock.

### Keeping it running 24/7

It is a single long-running process with a SQLite file for state, so anything that restarts a
process works. The only real requirement is that it stays running — a laptop that sleeps will
miss exactly the listings you care about, so a cheap always-on VPS or a Raspberry Pi is worth
more here than any tuning.

On Linux, as a user service (`~/.config/systemd/user/stanradar.service`):

```ini
[Unit]
Description=stanradar
[Service]
WorkingDirectory=/home/you/stanradar
ExecStart=/home/you/stanradar/.venv/bin/stanradar watch
Restart=always
RestartSec=30
[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now stanradar
journalctl --user -u stanradar -f
```

On Windows, Task Scheduler with "run whether user is logged on or not" and a trigger at
startup, pointing at `.venv\Scripts\stanradar.exe watch`.

If a portal fails five polls in a row you get one Discord warning, not one per cycle. Polling
continues regardless — one portal being down never stops the others.

## Filtering by commute, not by district

"Novi Beograd, or anywhere within a 20-minute bus ride" is a travel-time radius, not a
district list — and the difference matters, because the cheapest areas you can actually
reach are across the Sava in Čukarica (Banovo brdo, Žarkovo) and in the west of Novi Beograd
(Bežanijska kosa, Ledine). A district list would either miss them or sweep in half the city.

```yaml
criteria:
  commute:
    anchor: navigator      # Navigator Business Center, Blok 43, Novi Beograd
    max_minutes: 25
    areas: {zarkovo: 18}   # corrections, merged over the built-in estimates
    unknown: include       # keep listings in areas the table does not recognise
```

The per-area minutes live in [src/stanradar/commute.py](src/stanradar/commute.py) and are
**hand-estimated, not measured routes** — easily ±5 minutes out. They are organised in two
tiers: specific neighbourhoods, and municipality averages used only when no neighbourhood
matches. Correct any of them in `config.yaml` rather than editing the file.

`unknown: include` is deliberate. Portals invent neighbourhood names constantly, and on a
tight budget you would much rather check a map than miss a flat. Add a new anchor by adding
an entry to `ANCHORS` in that file.

## Tuning

Two numbers control the signal-to-noise ratio:

- `scoring.alert_threshold` (default 60) — below this a listing is recorded but not sent.
- `discord.mention_min_score` (default 85) — only these ping you. Leave `discord.mention`
  empty at first; add `@here` once you trust the scores, or you will mute the channel.

Run for a day, then `stanradar recent -n 50` and look at the `rejected_for` column. If good
flats are being dropped, that column tells you exactly which rule did it.

Costs: the portals are free. The AI scoring is the only running cost, and it is bounded by
`scoring.llm.min_rule_score` — raise it to grade fewer listings, or set
`scoring.llm.enabled: false` to run on rules alone.

## Development

```bash
pytest          # 68 tests, no network
ruff check src tests
```

The code is organised so that adding a portal means writing one file:

| File | Role |
|---|---|
| `sources/` | One adapter per portal. Implement `fetch_new()`, optionally `enrich()`. |
| `models.py` | The normalized `Listing` every adapter produces. |
| `scoring.py` | Hard filters and the rule score. |
| `commute.py` | Travel-time estimates per area, and the anchor table. |
| `llm.py` | The Claude vision call and its structured verdict. |
| `store.py` | SQLite de-duplication and alert history. |
| `notify.py` | Discord embeds. |
| `runner.py` | The poll loop and the pipeline order. |

Adapters return a `Listing` and nothing else — all filtering, scoring and alerting is shared,
so a new portal needs no changes anywhere else. `docs/SOURCES.md` records what each portal
returned when probed, including the two that fingerprint TLS and the one whose rental URL
silently redirects to sale listings.
