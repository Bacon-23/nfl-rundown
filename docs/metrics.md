# Metric definitions

Every number the Rundown publishes is defined here, and every definition is
surfaced to readers as a tooltip. The point is simple: we should never print a
figure we cannot explain when someone asks where it came from.

Sections marked **Not yet implemented** are specified but not built. They land
in Phase 2. Odds, weather, and injuries are live.

---

## Odds and market data

### Spread

Taken from the configured book (`ODDS_BOOK`, default DraftKings) via The Odds
API. Stored from the **favorite's** perspective: `spread = -4.5` with
`spread_favorite = "SEA"` means Seattle is laying 4.5.

A pick'em is stored as `0.0` and rendered `PK`, which is deliberately distinct
from a missing line (`null`, rendered `--`).

When the API is unavailable, the line comes from nflverse's `spread_line`,
which is **home-relative and positive when the home team is favored**. The
conversion is in `pipeline/sources/odds.py::_fallback_odds`.

### Opening line

Not fetched. The historical-odds endpoint costs ten times a normal call, so the
pipeline instead records the **first line it sees in a given week** and never
revises it. The guard is in SQL (`WHERE opening_line IS NULL`), so two
concurrent runs cannot race each other into overwriting it.

Displayed only when the line has actually moved: `SEA -4.5 (opened -3.5)`.

If the week's first run is missed, `--backfill-open` hits the historical
endpoint once as a repair. That is an exception, not the normal path.

### Total

The posted over/under from the same book, with the nflverse `total_line` as
fallback.

### Team totals

Preferred: the posted `team_totals` market, which requires the per-event
endpoint (one credit per game).

Derived when not posted:

```
favorite total = total/2 + |spread|/2
underdog total = total/2 - |spread|/2
```

Worked example, matching the mockup: total 45.5 with SEA -4.5 gives SEA 25.0
and New England 20.5.

Each side is rounded to the nearest half point, because that is what books
post. The two sides therefore need not re-add to exactly the game total — that
is correct behavior, not a rounding bug.

Derived values set `team_totals_derived: true`, which the admin screen shows so
a writer knows whether they are quoting a market or an inference.

### ATS and over/under records

**Not yet implemented.**

Computed from completed games in nflverse:

- **ATS**: compare `result` (home score minus away score) against
  `spread_line`. Equal is a push, counted separately, never as a win.
- **O/U**: compare `total` (combined points) against `total_line`. Equal is a
  push.

---

## Team efficiency

**Not yet implemented.** All from `nflreadpy.load_pbp()`.

### Pass and rush rate

Plays where `play_type` is `pass` or `run`, excluding `qb_kneel` and
`qb_spike`. Kneels and spikes are clock management, not play-calling, and
including them distorts late-game teams.

### PROE (pass rate over expected)

The mean of nflfastR's `pass_oe` over plays where it is non-null. Labeled
"full season" to match the mockup. No additional win-probability filter, so the
number matches what other public sources report.

### Pace (seconds per play)

Mean elapsed `game_seconds_remaining` between consecutive plays of the same
offensive possession, restricted to:

- neutral win probability (0.20 to 0.80), and
- first and second down.

"Pace" has no single industry definition, so this one is stated explicitly.
The filters exist because trailing teams hurry and leading teams stall, which
says more about the scoreboard than about the offense.

### Plays per game

Offensive plays (pass plus run, kneels and spikes excluded) divided by games
played.

### EPA per play and rank

Mean offensive EPA on pass and run plays, ranked 1 to 32 across the league.

---

## Passing game

**Not yet implemented.**

### Target share

Player targets divided by team targets, season to date.

### Target rate — read this before publishing it

The mockup calls this column **TPRR** (targets per route run). True TPRR
requires charted route data from PFF, FTN, or SIS, which we do not license.

What we publish instead:

```
target rate = targets / (offensive snap share x team dropbacks)
```

That is targets per *estimated pass snap*. It correlates well with TPRR and
ranks players in a similar order, but it is not the same statistic — a receiver
who sits out passing downs will look better than a true routes-run measure
would show.

Therefore:

- the column is labeled **`TGT RATE`**, never TPRR;
- the tooltip reads *"targets per estimated pass snap — a proxy for TPRR, which
  requires charted route data"*;
- the computation is isolated in `metrics/passing.py::target_rate()` so that
  licensing a real feed later is a one-function swap.

### Receiving yards per game

Receiving yards divided by games with at least one offensive snap. Games a
player missed entirely do not drag their average down.

---

## Running back workload

**Not yet implemented.**

- **Snap share** — Pro Football Reference `offense_pct`, via
  `load_snap_counts()`.
- **Rush attempts per game** — attempts divided by games with a snap.
- **Target share** — as above.
- **Yards per attempt** — rushing yards divided by attempts.

---

## Weather

Open-Meteo hourly forecast at the venue's coordinates, for the hour of kickoff.

**Roof type comes from `pipeline/sources/venues.py`, not from the schedule.**
nflverse's `stadium_id` and `roof` columns both carry the *home team's*
stadium, not the venue actually in use. For 2026 that means `LAX01` appears
against both SoFi Stadium and the Melbourne Cricket Ground, and the MCG — an
open-air ground — is reported as a dome. Trusting that column would forecast
Los Angeles weather for a game in Australia.

- **Fixed roofs** ("Indoors, no weather factor") skip the API call entirely.
- **Retractable roofs** still get a forecast, tagged "(retractable roof)". Open
  or closed is a game-day decision, not a property of the venue.
- **Beyond 16 days** the field reads "TBD". Open-Meteo does not forecast that
  far, and a made-up temperature is worse than an honest gap.
- An unknown venue costs one cell and logs a warning naming the stadium, rather
  than failing the build. Add it to `venues.py` when that happens.

Wind is only mentioned at 12 mph or above, and precipitation at 30% or above —
below those thresholds it is noise rather than a factor.

---

## Injuries

Primary source is ESPN's public injuries endpoint: all 32 teams in one request.

**It is a news feed, not an injury report.** It returns the 25 most recent
items per team, and roughly two-thirds carry the status "Active" — signings,
returns, roster notes. Those are dropped. Only statuses that bear on
availability are kept: Out, Injured Reserve, Doubtful, Suspension,
Questionable, Probable.

Rows are ordered by severity, with **Out above Injured Reserve**: a player
ruled out this week is news for this matchup, while someone on IR left the
picture weeks ago. Six rows per team, so the cap only ever drops the least
consequential entries.

The NOTE column is built from the structured injury type ("Knee - ACL"), not
from `shortComment`, which is wildly inconsistent — sometimes a full sentence,
sometimes the literal string "ir". The beat-writer comment is carried
separately for the admin screen, and dropped when it merely repeats the status.

nflverse's `load_injuries()` supplies practice participation (DNP / LP / FP),
which ESPN lacks. Enrichment only, wrapped so it can never break a build: that
feed's 2025 file last updated in March 2026 and cannot be assumed live.

### When ESPN cannot be reached

The pipeline **omits the `injuries` key entirely** rather than sending an empty
list. "I could not read the feed" and "nobody is hurt" are different claims,
and publishing the second when the first is true would blank a good injury
table off a live page.

WordPress carries the stored value forward for any key absent from an incoming
payload — see `STICKY_KEYS` in `storage.php`. A key that is *present* but empty
is honoured, because that is the pipeline actively saying the list is empty.

---

## Sample size and the early season

Season-to-date stats do not exist in Week 1. Rather than publish a three-game
sample as though it were settled, every stat module carries a `basis` and a
visible badge:

| Weeks | Basis | Badge |
|---|---|---|
| 1 | Prior season, full year | `2025 season` |
| 2 to 4 | Current season to date | `n = X games` |
| 5+ | Current season to date | none |

The cutovers are `PRIOR_SEASON_THROUGH_WEEK` and `SMALL_SAMPLE_THROUGH_WEEK` in
`pipeline/config.py` — one constant each, not logic scattered across modules.

Odds, weather, and injuries are exempt. They are always current.
