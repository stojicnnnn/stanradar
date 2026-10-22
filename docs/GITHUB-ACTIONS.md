# Running on GitHub Actions (free, no card)

Written for: you, setting this up without paying for a server.

GitHub runs the poller on a schedule instead of a VPS doing it continuously. It costs
nothing and needs no payment method. **Read the trade-off below before relying on it** —
it is a real downgrade, just not a fatal one.

## What you give up

| | VPS | GitHub Actions |
|---|---|---|
| Poll interval | 60 seconds | 10 minutes, *requested* |
| What actually happens | 60 seconds | **typically 5–20 min, occasionally longer** |
| Cost | ~€5/month | free |
| Card needed | yes | no |

GitHub's own documentation says scheduled workflows "may be delayed during periods of high
load", and the highest-frequency crons are the first thing to slip. Runs are occasionally
skipped entirely. Plan for ~5–20 minutes.

The cron asks for every 10 minutes rather than the 5-minute floor on purpose: GitHub drops
frequent schedules first when Actions is backed up, and since the real cadence is 5–20
minutes either way, asking for 5 buys nothing.

**Expect the first scheduled run to be slow to appear.** On this repo it had not fired
over 100 minutes — but GitHub Actions was under two overlapping incidents for that entire
window, so that says nothing about your setup. Check
[githubstatus.com](https://www.githubstatus.com) before concluding anything is wrong with
the workflow; `workflow_dispatch` keeps working when scheduling does not, because the two
are queued separately.

Since Belgrade listings go in the first hour or two, that still puts you in the first wave —
it just stops being *first*. If your card starts working, move to the VPS in
[DEPLOY.md](DEPLOY.md); nothing else about the project changes.

## The repository has to be public

This is the part that surprises people, so it is worth being precise:

- **Public repos get unlimited Actions minutes.**
- **Private repos get 2,000 minutes/month.** A 5-minute schedule is ~8,600 runs a month at
  1–2 billed minutes each, so roughly 9,000–17,000 minutes. You would run out in the first
  week, every week.

So: public repo. There is no configuration that makes a private repo work at this frequency.

**Before you make it public, know what becomes visible.** No secrets are at risk — the
webhook and API key live in GitHub Secrets, `.env` is gitignored and has never been
committed (verified). What *is* visible is `config.yaml`: your budget and the areas you
are looking in. The workplace has deliberately been reduced to "Blok 43, Novi Beograd"
rather than naming the building, in both `config.yaml` and the commute anchor, since a
public repo tied to your GitHub account would otherwise say exactly where you work. The
commute filter never reads that prose, so making it vaguer costs nothing; making it more
specific again is a one-line edit if you decide you do not care.

## Setup

**1. Push the repo to GitHub as public.**

```bash
cd ~/stanradar   # or C:\Users\sniko\code\stanradar
gh repo create stanradar --public --source=. --push
```

(Or create it in the web UI and `git remote add origin ... && git push -u origin main`.)

**2. Add the secrets.** Repo → Settings → Secrets and variables → Actions → New repository
secret:

| Name | Value |
|---|---|
| `DISCORD_WEBHOOK_URL` | your Discord webhook URL |
| `ANTHROPIC_API_KEY` | optional; omit and it runs on rule scores only |

**3. Trigger the first run by hand.** Actions tab → **watch** → *Run workflow*.

Watch that first run's log. This is the moment of truth — see below.

**4. Confirm the schedule took over.** After 10–15 minutes there should be further runs in
the Actions tab that you did not start. The first scheduled run after enabling a workflow
can take up to ~15 minutes to appear.

## The one thing that might not work

**The portals may block GitHub's IP ranges.** halooglasi and nekretnine.rs already sit
behind WAFs that fingerprint TLS, and blocking datacenter IP ranges is the other common
thing those WAFs do. GitHub's runners are Azure IPs, which are heavily blocked across the
web. Whether these five Serbian sites block them is genuinely unknown until you run it —
it cannot be tested from a home connection.

**Measured on the first real run (2026-10-22):** four of five work from GitHub's runners.

| Portal | From GitHub Actions |
|---|---|
| 4zida.rs | works |
| nekretnine.rs | works |
| oglasi.rs | works |
| kupujemprodajem.com | works |
| **halooglasi.com** | **HTTP 403 — blocked** |

Interesting detail: nekretnine.rs uses the same class of TLS-fingerprinting WAF and passes,
so halooglasi is blocking the IP range rather than the handshake. A different `impersonate`
profile will not get around that. It is commented out of `sources` in `config.yaml` for
this reason, with a note on how to restore it — it works fine from a home connection or a
VPS, so moving off Actions brings it back by deleting one `# `.

If you set this up fresh, the first manual run tells you which world you are in:

- **Green, and alerts arrive** → it works, you are done.
- **Green, but one portal logs `fetch failed`** → that portal blocks Azure; the other four
  carry on. After 5 consecutive failures you get a single Discord warning, not one per run.
- **Red, `every source failed this cycle`** → all five block the runner. Actions is not
  viable and the answer is a VPS, a Raspberry Pi, or any always-on machine at home.

Check with:

```
Actions tab → watch → latest run → "Poll the portals" step
```

A blocked portal shows as `HTTP 403` or a timeout in that log.

## How state survives between runs

Each run is a fresh machine, so "which listings have I already alerted on" is kept in the
Actions cache (`data/stanradar.db`), restored at the start of a run and saved at the end.

Two consequences worth knowing:

- **If the cache is lost** (GitHub evicts entries unused for 7 days, or on a repo-wide 10 GB
  limit), the next run re-primes: it records everything currently listed as already-seen and
  alerts on none of it. You get the five "now watching" messages again instead of 100 alerts.
  That is the priming logic in `runner.py` doing its job.
- **Runs never overlap.** The `concurrency` group queues them, because two runs restoring
  the same cache would both decide the same listing is new and you would get doubles.

## The 60-day trap

GitHub **disables scheduled workflows in public repos after 60 days with no repository
activity** — and workflow runs do not count as activity, only commits do. Left alone, this
would go silent after two months with no notification.

`keepalive.yml` handles it by committing a timestamp every Monday. Leave it enabled. If you
ever see the watcher stop, check the Actions tab for a banner offering to re-enable it.

## Day to day

```bash
# Change your criteria:
nano config.yaml && git commit -am "tune criteria" && git push
```

The workflow reads `config.yaml` from the repo on every run, so a push is the deploy.

To pause alerts, disable the **watch** workflow in the Actions tab. To check what it has
been doing without digging through logs, the Discord channel is the log — and
`stanradar recent -n 40` works locally against your own copy if you want the rejection
reasons.

## Seeing what is live right now

`watch` only ever reports listings it has not seen before, which is what you want running
24/7 but useless for "show me the current stock". `scan` answers that instead:

```bash
stanradar scan --profile shared-3 --pages 8          # print them
stanradar scan --profile shared-3 --pages 8 --send   # post them to Discord
```

It ignores the seen-state and **records nothing**, so it cannot make the watcher skip a
later alert. `--pages` sweeps deeper than one page where the source supports it (4zida
does; the others serve one page), which matters for a rare profile — the newest 25
listings will almost never contain a three-bedroom place.

## Fallback: trigger from an external cron

GitHub's *scheduler* and its *dispatch API* are separate systems, and they fail
independently. Measured on this repo: over 131 minutes with Actions degraded, `schedule`
produced **zero** runs while `POST .../dispatches` kept returning `204` every time.

So if the schedule stays dead, drive the workflow from outside GitHub. This is not a
workaround for a broken config — it is a genuinely more reliable trigger, because a free
external cron service only has to do one thing and GitHub only has to accept one API call.

**1. Create a fine-grained token.** github.com → Settings → Developer settings →
Fine-grained tokens → Generate new token:

- Repository access: **Only select repositories** → `stanradar`
- Permissions → Repository → **Actions: Read and write** (nothing else)
- Expiration: 90 days or whatever you will remember to rotate

Scoped this way the token can start a workflow in this one repo and nothing more. It cannot
read your other repos, push code, or change settings. The repo is public anyway, so it
grants no read access you have not already given the world.

**2. Point a free cron at it.** [cron-job.org](https://cron-job.org) is free and needs no
card. Create a job:

| Field | Value |
|---|---|
| URL | `https://api.github.com/repos/<you>/stanradar/actions/workflows/watch.yml/dispatches` |
| Method | `POST` |
| Schedule | every 10 minutes |
| Header | `Authorization: Bearer <your token>` |
| Header | `Accept: application/vnd.github+json` |
| Body | `{"ref":"main"}` |

A success is **HTTP 204 No Content** with an empty body — cron-job.org may show that as a
failure if it expects 200, so check the response code rather than the status icon.

**3. Turn the GitHub schedule off** so the two do not both fire. Either delete the
`schedule:` block from `watch.yml`, or leave it — the `concurrency` group already stops two
runs overlapping, so a duplicate trigger queues rather than double-alerting.

The cost is that a third party holds a token that can start one workflow in one repo. If
that is not a trade you want, the alternatives are a VPS ([DEPLOY.md](DEPLOY.md)) or any
always-on machine at home.
