# NFL Rundown — Automated Weekly Matchup Dashboard for WordPress

## Context

Trinity Analytics publishes a weekly NFL article breaking down every matchup. Today it's pure text, hand-written each week. `NFL_Rundown_Mockup.docx` shows the target: the same editorial voice, but wrapped around a research dashboard — odds bar, injuries, team efficiency, target share, RB workload, DvP — with the writer's Scouting Notes, TD Leans, and Score Prediction interleaved.

The problem is labor. Hand-assembling PROE, pace, snap shares, and target share for 16 games is hours of copy-paste per week, and the numbers go stale the moment they're pasted. The goal is that **the stats pull themselves and the writer only writes.**

Outcome: a Python pipeline on GitHub Actions computes every number from public data plus the existing Odds API subscription and pushes it into WordPress; a custom plugin gives the writer one admin screen listing all 16 games with note fields; hitting Publish freezes the numbers and renders the dashboard into a single weekly post.

**This revision** adds the environment topology. All testing happens on the WordPress.com staging site: it is the only target of scheduled builds, the only place the plugin runs before launch, and where the full rehearsal week happens. Production stays untouched — no automated writes, no automatic deploys — until a rehearsal passes. The plan below is otherwise unchanged from the approved version; Phase 0's remaining work and the verification section are the parts that moved.

### Decisions locked in

| Decision | Choice |
|---|---|
| Hosting | WordPress.com **Business** (custom plugins + SFTP + GitHub Deployments available) |
| Testing | **All testing on the staging site.** Nothing automated touches production until a full rehearsal week passes |
| Promotion | **Separate GitHub Deployments per site** — staging auto-deploys from `main`, production is a manual deploy |
| Staging odds | **Record once, replay** — one live capture committed as a fixture; staging replays it |
| Scheduled runs | **Staging only** for now; production is a one-variable switch at launch |
| Pipeline | **GitHub Actions**, Python, pushes JSON to WP over REST |
| Article shape | **One weekly post**, games as accordion/tabs |
| Editing | **Admin dashboard**, all games on one screen |
| Odds | **The Odds API**, 100K tier — one named book, consensus never shown |
| Team totals | Real `team_totals` market (derived arithmetic as fallback) |
| Line movement | **Yes, text only** — "SEA -4.5 (opened -3.5)" |
| Player props | **Deferred to Phase 5** |
| TPRR | **Free proxy, honestly labeled** — column renamed `TGT RATE` |
| Freshness | **Freeze at publish** (snapshot + "stats as of" timestamp) |
| Timeline | **Week 1** — kickoff is **2026-09-09** (NE @ SEA), 18 days out |
| V1 scope | Everything except **DvP** (deferred to Phase 5) |

> **One open config value:** which book to publish. `ODDS_BOOK` in `pipeline/config.py` defaults to `draftkings` — change the string if you'd rather publish FanDuel or another. It's a one-line edit, not a rebuild.

### What I need from you before Phase 0 can finish

Five things, all on your side of a login. Everything else is already built or
is mine to write.

1. **Enable SSH on the staging site** — WordPress.com → Hosting → Overview, on
   the *staging* site. Gives WP-CLI, which is both a debugging tool and the
   fallback ingest path.
2. **The staging site URL** and its SFTP/SSH credentials.
3. **A staging token in `wp-config.php`** — generate with
   `openssl rand -hex 32`, then add above the "stop editing" line:
   `define( 'TRINITY_RUNDOWN_TOKEN', '<the string>' );`
4. **A GitHub repo** to push to, with GitHub Deployments connected to staging
   (deploy `wordpress/trinity-rundown` → `/wp-content/plugins/trinity-rundown`).
5. **Your Odds API key**, as a repo secret.

I can do everything up to the point of needing these, then stop.

### Data sources — verified live during planning

| Need | Source | Status |
|---|---|---|
| Spread, total, moneyline | **The Odds API** `/v4/.../odds`, `bookmakers=<ODDS_BOOK>` | paid, authoritative for what we publish |
| Team totals | **The Odds API** `team_totals` (per-event endpoint) | falls back to `total/2 ± spread/2`, which reproduces the mockup exactly (45.5 / -4.5 → 25.0 & 20.5) |
| Opening line | **self-recorded** on the week's first run (see below) | avoids the 10× historical-odds cost |
| Schedule, kickoff, TV, roof, surface, stadium | `nflverse/nfldata` `games.csv` | ✅ 2026 rows present (272) |
| Odds fallback / backfill | same file (`spread_line`, `total_line`, moneylines) | ✅ refreshes every ~5 min in-season |
| ATS + O/U season records | derived from historical `games.csv` | ✅ |
| PROE, EPA/play, pass/rush rate, pace, plays/gm | `nflreadpy.load_pbp()` | ✅ nightly + gameday updates |
| Snap share | `load_snap_counts()` (PFR) | ✅ 4×/day in-season |
| Targets, target share, rec yds | `load_player_stats()` | ✅ |
| Injuries — game status + note | **ESPN public API** `site.api.espn.com/.../nfl/injuries` | ✅ tested: all 32 teams, status + `shortComment` |
| Injuries — practice participation (DNP/LP/FP) | `load_injuries()` | ⚠️ `injuries_2025.parquet` exists but last updated Mar 2026 — *enrichment only*, never a hard dependency |
| Weather | Open-Meteo forecast + 32-stadium lat/long lookup | free, no key; only valid inside 16 days |
| Team colors / logos | `nfldata` `teamcolors.csv`, `logos.csv` | ✅ used for per-game theming |

**Not available at any price we're paying:** true TPRR needs charted routes run (PFF/FTN/SIS). Shipping a labeled proxy instead — see Metric Definitions.

---

## Architecture

One GitHub repo holds both halves. WordPress.com **GitHub Deployments** (Business-plan feature) deploys the plugin directory — automatically to staging on every push to `main`, manually to production. GitHub Actions runs the pipeline on cron, currently pointed at staging.

```
nfl-rundown/
├── pipeline/                          Python 3.12 · nflreadpy (polars) · httpx
│   ├── sources/     odds.py  schedule.py  pbp.py  snaps.py
│   │                injuries.py  weather.py  team_map.py
│   ├── metrics/     efficiency.py  passing.py  rushing.py  records.py
│   ├── build_week.py                  → assembles the week payload
│   ├── push.py                        → POSTs to WordPress
│   ├── config.py                      → ODDS_BOOK, refresh cadence, season cutovers
│   ├── schema.py                      → payload contract (pydantic)
│   └── tests/       golden-file tests vs. committed 2025 fixtures
├── .github/workflows/build-week.yml   cron + workflow_dispatch
└── wordpress/trinity-rundown/         PHP plugin → /wp-content/plugins/
    ├── trinity-rundown.php
    ├── includes/  rest-ingest.php  storage.php  admin-week.php
    │               render.php      shortcode.php  cli.php
    └── assets/    rundown.css  rundown.js
```

### Data flow

1. **Cron** (hourly Tue–Sun during the season, plus manual `workflow_dispatch`) runs `build_week.py --season 2026 --week auto`. The workflow declares a GitHub Environment — `staging` today, `production` at launch — which is what selects the target site and its token. `build-week.yml` gains an `environment` input defaulting to `staging`.
2. Fetch all sources, compute all metrics, emit **one JSON payload per week** containing a list of game objects. Staging runs add `--replay-odds`.
3. `push.py` POSTs to `/wp-json/trinity-rundown/v1/week` with `Authorization: Bearer <token>`. Idempotent upsert keyed on `(season, week, game_id)`.
4. Plugin writes to custom table `wp_trinity_rundown_games`.
5. Writer opens **Rundown → Week N**, fills in notes, hits **Publish**.
6. Publish creates/updates the weekly post containing one shortcode, and **freezes**: copies `stats_json` → `published_json`, sets `locked = 1`.
7. Front end renders server-side PHP from `published_json` (or `stats_json` if not yet locked).

### Environments

Two WordPress sites, one repo, one pipeline. The only difference between them
is which secrets a workflow run reads.

| | Staging | Production |
|---|---|---|
| URL | `staging-xxxx.<domain>` (auto-generated, not customisable) | the live domain |
| Plugin deploy | GitHub Deployments, **automatic** on push to `main` | GitHub Deployments, **manual** ("Deploy now") |
| Database | its own, including its own `wp_trinity_rundown_games` | live |
| Scheduled builds | **yes** — this is the cron target for now | none until launch |
| Odds | replayed from a committed fixture | live Odds API |
| Secrets | GitHub Environment `staging` | GitHub Environment `production` |

Each site gets its **own** `TRINITY_RUNDOWN_TOKEN`. They are separate
credentials for separate databases; reusing one would mean a mistyped
`WP_SITE_URL` silently writes to the wrong site.

Secrets live in **GitHub Environments** rather than plain repo secrets, so
`WP_SITE_URL` and `TRINITY_RUNDOWN_TOKEN` are scoped to the environment a job
declares. `ODDS_API_KEY` stays a repo-level secret, since both use the same
subscription.

**Do not use WordPress.com's "Push to Production" sync.** Its dialog offers to
copy the database, and one wrong checkbox overwrites live posts with staging
content. Plugin code reaches each site from git, independently. The sync
feature has no role in this workflow.

Staging also needs **SSH enabled** (Hosting → Overview → SSH on the staging
site). That unlocks WP-CLI, which is both the fallback ingest path and the
fastest way to inspect what actually landed in the table.

#### Open question, settled on day one

WordPress.com staging sites are noindexed, and the documentation does not say
whether they answer anonymous HTTP requests. The REST push depends on that.

The first task in Phase 0 is therefore a `/health` probe from outside. Two
outcomes, both already covered:

- **Reachable** — the REST ingest path works as designed; nothing changes.
- **Gated** — the pipeline writes its payload to disk and a workflow step
  copies it over SSH and runs `wp rundown seed --file=...`. Same data, same
  storage code, different transport. `push.py` gains a `--transport ssh`
  option and everything downstream is unaffected.

This is checked before any other work, because it is the only finding that
would change the shape of the build.

### The core invariant

```sql
CREATE TABLE wp_trinity_rundown_games (
  id             BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  season         SMALLINT     NOT NULL,
  week           TINYINT      NOT NULL,
  game_id        VARCHAR(32)  NOT NULL,   -- '2026_01_NE_SEA'
  stats_json     LONGTEXT     NOT NULL,   -- pipeline-owned, overwritten freely
  overrides_json LONGTEXT     NULL,       -- human-owned, per-field corrections
  notes_json     LONGTEXT     NULL,       -- human-owned editorial text
  published_json LONGTEXT     NULL,       -- frozen snapshot at publish
  opening_line   LONGTEXT     NULL,       -- first odds seen this week, write-once
  locked         TINYINT(1)   NOT NULL DEFAULT 0,
  sort_order     SMALLINT     NOT NULL DEFAULT 0,
  updated_at     DATETIME     NOT NULL,
  UNIQUE KEY uq_game (season, week, game_id)
);
```

**Stats and editorial live in separate columns.** A pipeline refresh writes only `stats_json`; it can never clobber a writer's paragraph. Rendering merges `stats_json` ← `overrides_json` ← `notes_json` in that order. This is the single most important structural decision in the build.

---

## Odds integration

### Endpoint plan and credit budget

Verified against the v4 docs: the bulk endpoint charges `markets × regions` and returns **all games in one call**; the per-event endpoint charges `unique markets returned × regions` **per event**; historical odds charge **10×**. Passing `bookmakers=` in place of `regions=` keeps the multiplier at 1.

| Call | Endpoint | Cost each | Cadence | Season total (18 wks) |
|---|---|---|---|---|
| `h2h,spreads,totals` | bulk `/odds` | 3 | hourly | ~9,100 |
| `team_totals` | per-event × 16 | 16 | 4×/day | ~8,100 |
| **Total** | | | | **~17K of 100K/mo** |

Enormous headroom — the cadence in `config.py` can be turned up substantially, and Phase 5 props fit without a tier change.

### Opening lines without paying 10×

Rather than calling the historical endpoint, the pipeline **records the first odds it sees each week** into `opening_line` and never overwrites it (write-once at the DB layer, so a bad re-run can't corrupt it). The Tuesday 12:00 UTC run establishes the opener. If a run is missed, `build_week.py --backfill-open` hits the historical endpoint once as a repair path — an exception, not the normal flow.

Rendering: `SEA -4.5 (opened -3.5)`. The parenthetical is suppressed when the line hasn't moved.

### Record and replay, for staging

Staging must exercise the real parsing path without spending credits on every
test run, and test results must not shift under you between runs.

Two flags on `build_week.py`:

- `--record-odds <path>` — do a normal live fetch and additionally write every
  raw response (the bulk call plus each per-event `team_totals` call, keyed by
  event id) to a fixture file, with a capture timestamp. Costs one normal
  build's credits.
- `--replay-odds <path>` — serve those recorded responses instead of calling
  the API. Zero credits, byte-identical results every time.

The fixture is committed at `pipeline/fixtures/odds-live-<season>-wk<NN>.json`.
Staging workflow runs pass `--replay-odds`; production passes nothing and goes
live.

**The stale-fixture trap.** Replay matches events to games by team pair, the
same as the live path. Replay a Week 1 fixture during Week 5 and *nothing*
matches, every game quietly falls back to nflverse lines, and a broken odds
parser would still look like a passing test.

So replay reports its hit rate and fails the run when it drops below half:

```
replay: matched 16/16 games (fixture recorded 2026-08-24)
replay: matched 0/16 games (fixture recorded 2026-08-24) -- stale,
        re-record with --record-odds
```

A test that cannot fail is not a test. Re-record whenever the week rolls over
or the payload shape changes.

### Team-name mapping

The Odds API uses full names (`Seattle Seahawks`); nflverse uses abbreviations (`SEA`). `sources/team_map.py` builds the lookup from `nfldata/teams.csv` and joins events on `(kickoff date, home team, away team)`. **Any unmapped event is a hard error, never a silent skip** — a missing join would otherwise publish a game with no line. Relocations and name changes are the known failure mode; the mapping table is tested.

### Failure behavior

Odds API down or over quota → fall back to nflverse `games.csv` (`spread_line`, `total_line`, moneylines), flag the payload `odds_source: "nflverse_fallback"`, and surface that in the admin screen so the writer knows before publishing. The page never shows a blank odds bar.

---

## Metric definitions

These get written into `docs/metrics.md` and surfaced as tooltips on the page. Publishing a number you can't defend is the main reputational risk here.

- **Pass / rush rate** — `play_type in ('pass','run')`, excluding `qb_kneel` and `qb_spike`.
- **PROE** — mean of nflfastR's `pass_oe` over plays where it is non-null. Labeled "full season" to match the mockup.
- **Pace (sec/play)** — mean elapsed `game_seconds_remaining` between consecutive plays of the same offensive possession, restricted to neutral win probability (0.20–0.80) and 1st/2nd down. Documented, because "pace" has no single industry definition.
- **Plays/gm** — offensive plays (pass + run, kneels/spikes excluded) ÷ games played.
- **EPA/play (rank)** — mean offensive EPA on pass+run, ranked 1–32.
- **Target share** — player targets ÷ team targets, season to date.
- **`TGT RATE` (TPRR proxy)** — `targets ÷ (offense_snap_pct × team dropbacks)`. Column is **not** called TPRR; tooltip reads *"targets per estimated pass snap — a proxy for TPRR, which requires charted route data."* Isolated in `metrics/passing.py::target_rate()` behind one adapter so a paid routes feed drops in later without touching anything else.
- **Rec yds/gm** — receiving yards ÷ games with ≥1 offensive snap.
- **RB workload** — PFR `offense_pct`, rush att/gm, target share, yds/att.
- **ATS record** — `result` (home − away) vs. `spread_line`; equal = push.
- **O/U record** — `total` vs. `total_line`; equal = push.
- **Team totals** — market line from `team_totals`. If absent for a game, derive `home = total/2 + spread/2`, `away = total/2 − spread/2`, round to 0.5, and mark the field as derived.
- **Weather** — Open-Meteo hourly forecast at stadium lat/long for the kickoff hour. If `roof in ('dome','closed')` → "Indoors, no weather factor". Beyond the 16-day forecast horizon → "TBD".

### Early-season fallback

Season-to-date stats don't exist in Week 1. One constant in `pipeline/config.py`, not logic scattered across modules:

- **Week 1** → prior season (2025) full-season data, every module badged **"2025 season"**.
- **Weeks 2–4** → current season to date, badged **"n = X games"** so small samples are visible to the reader.
- **Week 5+** → current season, no badge.

Odds and injuries are exempt — they're always current.

---

## Security

WordPress.com accounts sign in via WP.com SSO, where the Application Passwords UI is unreliable. So the plugin does **not** use Application Passwords:

- Plugin registers its own REST route with a `permission_callback` that checks a bearer token against a `TRINITY_RUNDOWN_TOKEN` constant defined in `wp-config.php` (added once over SFTP).
- `hash_equals()` for constant-time comparison; reject outright if the constant is undefined; HTTPS only.
- **A different token per site.** Staging and production are separate databases; a shared token would let a mistyped `WP_SITE_URL` write to the wrong one without complaint. Generate each with `openssl rand -hex 32`.
- Tokens live in GitHub **Environments** (`staging`, `production`), not repo-level secrets, so a job only ever holds the credential for the site it declares. `ODDS_API_KEY` is repo-level, since one subscription serves both.
- Route accepts only the ingest payload — it cannot create or edit posts. Publishing is a human action in wp-admin.
- The Odds API key lives only in Actions secrets. It is never sent to WordPress and never appears in the payload — including in a recorded fixture, which stores responses only.

---

## Rendering

- **Server-side PHP** emits full HTML for all 16 games, so Google indexes the writeups. JavaScript only enhances.
- Each matchup is a `<details>` element; `rundown.js` (~2 KB, no jQuery) upgrades them to tabs on desktop. Without JS the page still works.
- Scoped `.trundown-*` class prefix; per-game team colors injected as CSS custom properties from `teamcolors.csv`.
- Below 640 px the stat tables reflow into stacked cards. Print stylesheet included.
- A "Week at a glance" summary table sits above the accordion so the page has value before anything is expanded.
- The odds bar carries a visible book attribution and a "stats as of" timestamp.
- Accessibility: real `<table>` markup with `<th scope>`, sufficient contrast on team-colored headers, no color-only encoding of trend labels.

---

## Code changes this revision requires

Most of the staging work is configuration rather than code. What does change:

| File | Change |
|---|---|
| `.github/workflows/build-week.yml` | Add an `environment` input defaulting to `staging`, and the matching `environment:` job key so secrets resolve per site. Staging runs append `--replay-odds`. |
| `pipeline/sources/odds.py` | A recorder/replayer wrapping the two `client.get` calls in `_get_bulk` and `_apply_team_totals`. Record captures raw JSON keyed by call; replay serves it and counts matches. The parsing functions below it are untouched. |
| `pipeline/build_week.py` | `--record-odds <path>` and `--replay-odds <path>`, threaded into the existing `odds_source.fetch(games, use_api=...)` call. |
| `pipeline/config.py` | `ODDS_FIXTURE` default path and the replay hit-rate floor. |
| `pipeline/push.py` | Only if the `/health` probe says staging is gated: a `--transport ssh` branch that writes the payload and shells out to `wp rundown seed`. The existing `push_week` retry logic stays the primary path. |
| `pipeline/fixtures/` | New directory for the committed odds capture. |
| `pipeline/tests/test_odds_replay.py` | New: replay returns recorded values; a stale fixture (zero matches) fails rather than silently falling back. |
| `README.md` | Staging setup, the two environments, and the record/replay workflow. |

Nothing in `storage.php`, `render.php`, or `schema.py` changes — the environment
split is entirely upstream of them.

## Build phases

**Phase 0 — prove the loop on staging (day 1).** Already partly done: the repo,
plugin, schema, schedule and odds sources, and 33 passing tests exist locally.
What remains is everything that needs a real WordPress:

1. Enable SSH on the staging site; confirm `wp --info` runs.
2. Generate the staging token, add it to `wp-config.php` over SFTP.
3. Connect GitHub Deployments: staging ← `main`, auto.
4. Activate the plugin; confirm the table was created.
5. **Probe `/health` from outside** — this settles the anonymous-reachability
   question and picks the transport for everything after it.
6. Push a real Week 1 payload and view it on a staging page.

Nothing else starts until a payload built by CI is visible on a staging URL.

**Phase 1 — the no-history modules (days 2–5).** `weather.py` and
`injuries.py` (`odds.py`, `team_map.py`, `schedule.py` are done; `records.py`
remains). Completes the header/odds bar and the injury table. Deliberately
first because **neither needs season-to-date data — both are fully correct in
Week 1.** Also: record the odds fixture, and let the staging cron start
capturing opening lines so Week 1 has real movement data.

**Phase 2 — the stats modules (days 6–9).** `pbp.py`, `snaps.py`, `metrics/efficiency.py`, `passing.py`, `rushing.py`, plus the early-season fallback.

**Phase 3 — the writer's screen (days 10–13).** `admin-week.php`: all games on one page, read-only stats, textareas for Scouting Notes / TD Leans / Score Prediction, per-field override inputs, odds-source warning banner, Publish + freeze.

**Phase 4 — make it look like the mockup (days 14–17).** CSS, mobile, print, a11y, tooltips, all reviewed on staging URLs. Dry runs against real Week 1 odds as they firm up. One known issue to fix here: New England and Seattle share `#002244` as their primary color, so the team-color accent cannot distinguish that matchup — fall back to `team_color2` when the two sides collide.

**Phase 5 — go live, then extend.** Connect production GitHub Deployments and do a manual deploy; add the `production` environment secrets; switch the scheduled workflow's target. Then: DvP module; anytime TD props de-vigged into implied probabilities; "last 4 weeks" trend columns; paid routes-run adapter; line-movement sparkline.

---

## Verification

**Pipeline**
- `pytest pipeline/tests/` — golden-file tests build 2025 Week 10 from committed parquet fixtures and diff against expected JSON. This is the tripwire for nflverse schema drift; pin the `nflreadpy` version.
- Odds tests run against a recorded HTTP fixture (no live key needed in CI) and cover: normal response, over-quota 429, missing `team_totals` → derived fallback, and an unmappable team name → hard error.
- `python -m pipeline.build_week --season 2026 --week 1 --dry-run` writes JSON to disk instead of posting — inspect before any push.
- Track remaining quota from the `x-requests-remaining` response header; log it every run and fail loudly under a threshold.
- Hand-check three numbers per module against a public source (PROE vs. rbsdm.com, snap share vs. PFR, the spread against the book's own site) before Week 1 goes out.

**WordPress — all of this on staging**

Currently unverified: **no PHP has ever executed.** There is no PHP on the dev
machine and Docker is not running, so the plugin has only had a delimiter-balance
check. `php -l` and WordPress coding standards run in CI. Staging is where the
code first actually runs.

- `wp rundown status --season=2026 --week=1` over SSH shows what landed: rows, lock state, whether an opener was captured, whether notes exist.
- `wp rundown seed --file=build/2026-week-01.json` loads a payload with no pipeline involved, so plugin work never blocks on the data side.
- Round-trip test: push → confirm the row → confirm it renders → **push again and confirm `notes_json` survives unchanged.** This is the invariant that matters most; it gets its own explicit test.
- Write-once test: push twice with different odds, confirm `opening_line` did not change.
- Publish → verify `locked = 1`, `published_json` populated, and that a subsequent pipeline run does *not* change what the page displays.
- View the staging post on mobile, desktop, and with JS disabled.

**Environment isolation checks** — cheap, and they catch the failure that would
hurt most:

- Point a build at staging, then confirm production's table is still empty. Wrong-site writes should be impossible, not merely unlikely.
- Confirm the staging token is rejected by production and vice versa (expect `403`).

**End-to-end rehearsal, the week before kickoff.** On staging, run the full
pipeline against **2025 Week 1** — which exercises the prior-season fallback
that real Week 1 will use — write real commentary into the admin screen,
publish, and read the result on a staging URL. Then re-run the pipeline and
confirm the published page did not move. Nothing about Week 1 should be the
first time any of this runs.

---

## Risks

| Risk | Mitigation |
|---|---|
| nflverse schema drift breaks the build | Golden tests + pinned version; Actions failure notifies |
| ESPN injuries API is undocumented and unsupported | Cache last-good response; degrade to "no injuries reported" rather than failing the run; it's the only unofficial source in the stack |
| Odds API outage or quota exhaustion | nflverse odds fallback, flagged in the payload and shown in admin; quota logged every run |
| Book stops offering a market mid-season | Team totals derive from spread + total; other fields degrade to fallback rather than blank |
| Team-name mapping silently drops a game | Unmapped events are a hard error, with a tested mapping table |
| WordPress.com restricts something we need | Phase 0 tests exactly this on day 1, on staging |
| Staging does not answer anonymous requests, breaking the REST push | The day-1 `/health` probe settles it; SSH + `wp rundown seed` is the fallback transport, using the same payload and storage code |
| A stale replay fixture hides a broken odds parser | Replay reports its match rate and fails below 50% |
| A build writes to the wrong site | Separate token per site, secrets scoped to GitHub Environments, and an explicit cross-environment rejection test |
| WP.com "Push to Production" overwrites live posts | Not used. Plugin code reaches each site from git independently |
| Untested plugin code touches the live database | Scheduled builds point at staging only until launch; production deploys are manual |
| Week 1 has no current-season data | Prior-season fallback, explicitly badged |
| 18 days is tight | Phases 1–2 are independently shippable; worst case Week 1 publishes with the odds bar + injuries only, and the rest lands Week 2 |
| A published number is wrong | Per-field overrides in the admin screen; every metric definition documented and tooltipped |
