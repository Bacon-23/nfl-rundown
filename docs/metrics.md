# Metric definitions

Every number the Rundown publishes is defined here, and every definition is
surfaced to readers as a tooltip. The point is simple: we should never print a
figure we cannot explain when someone asks where it came from.

Everything described here is built and live as of Phase 2.

## Units, once, for everything below

**Every rate in the payload is a fraction between 0 and 1.** Pass rate is
`0.542`, target share is `0.362`, PROE is `0.029`. The renderer multiplies by
100 and adds the sign; nothing else does.

This matters because the upstream feeds disagree with each other. nflfastR
publishes `pass_oe` in percentage points (`2.9` for +2.9%) and it is divided by
100 on the way in; PFR's `offense_pct` already arrives as a fraction and is
left alone. Both conversions are asserted in `pipeline/tests/test_efficiency.py`
and in the live-feed tests, because a silent 100x error in either direction is
the kind of thing that looks plausible enough to publish.

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

**There is no repair path for a wrong opener.** `--backfill-open` is described
in older drafts of `plan.md` but was never implemented, and the plugin's
`TRUN_Storage::force_opening_line()` has no callers. Correcting an opener means
editing the row directly.

So the first build of a week is the one worth watching: it is the only write in
this system a re-run cannot repair, and a build that quietly falls back to
nflverse lines still reports success.

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

Computed in `pipeline/metrics/records.py` from completed games in nflverse:

- **ATS**: compare `result` (home score minus away score) against
  `spread_line`. Equal is a push, counted separately, never as a win.
- **O/U**: compare `total` (combined points) against `total_line`. Equal is a
  push.

nflverse's `spread_line` is home-relative and positive when the home team is
favored, so the home side covers when the margin exceeds it. That convention is
asserted in the unit tests and was confirmed against the 2025 season, where
favorites win outright about two thirds of the time on both sides of the sign.

**Regular season only.** Weeks 19 and up are the playoffs; a deep run would
otherwise inflate a record badged "2025 season" past what every other site
publishes.

Rendered as `12-5`, or `9-7-1` when a game pushed. A team with no completed
games is left empty rather than shown as `0-0`, which reads as a real record.
Week 1 shows the prior season's records, since the current one has none --
the cutover lives in `config.stats_season()`.

---

## Team efficiency

All from `nflreadpy.load_pbp()`, via `pipeline/metrics/efficiency.py`. Computed
for all 32 teams in one pass, because EPA rank is a league-wide statement that
cannot be worked out from the two teams in a matchup.

### Pass and rush rate

Plays where `play_type` is `pass` or `run`, excluding `qb_kneel` and
`qb_spike`. Kneels and spikes are clock management, not play-calling, and
including them distorts late-game teams.

### PROE (pass rate over expected)

The mean of nflfastR's `pass_oe` over plays where it is non-null. Labeled
"full season" to match the mockup. No additional win-probability filter, so the
number matches what other public sources report.

"Where it is non-null" is deliberately looser than the pass/rush filter above:
the model scores some snaps that filter drops, penalties above all — 1,482 of
2025's `no_play` rows carry a `pass_oe`. It declines to score others, and
substituting a zero for "no opinion" would drag every team toward neutral,
which is a different claim from the one we are making.

### Pace (seconds per play)

Mean elapsed `game_seconds_remaining` between consecutive plays of the same
offensive possession, restricted to:

- neutral win probability (0.20 to 0.80),
- first and second down, and
- gaps of a minute or less.

"Pace" has no single industry definition, so this one is stated explicitly.
The first two filters exist because trailing teams hurry and leading teams
stall, which says more about the scoreboard than about the offense.

The one-minute cap is the filter the original spec did not have. A raw
`game_seconds_remaining` delta absorbs timeouts, injuries, replay reviews, and
TV breaks; those are the broadcast, not the huddle. Real snap-to-snap intervals
sit around 30 seconds, so the cap only trims a tail. Gaps across a change of
possession are excluded too — the other team had the ball in between.

Measured over 2025 this puts the league between 29.4 and 35.0 seconds. That
runs a little slower than figures published elsewhere, because the interval
starts at the previous snap rather than at the moment the ball is spotted.
Comparing our number against another site's is comparing two definitions.

### Plays per game

Offensive plays (pass plus run, kneels and spikes excluded) divided by games
played.

### EPA per play and rank

Mean offensive EPA on pass and run plays, ranked 1 to 32 across the league.

---

## Passing game

Five receivers per team, ranked by target share, from
`pipeline/metrics/passing.py`. Roles (`WR1`, `TE1`, `RB1`) are numbered within
position across the whole team and assigned *before* the five-row cut, so a
team's WR3 is its third receiver rather than the third name that survived the
table.

### Target share

Player targets divided by team targets, season to date.

Counting stats are summed across every team a player suited up for, so a
midseason trade does not split him into two half-players. His *share*, though,
is computed against his primary team — the one he saw the most targets with —
because a share of two different denominators is not a number. This only bites
for players who actually moved during a season.

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

**Expect it to read high.** Snap share counts every offensive snap, including
runs, but a starting receiver runs a route on nearly every dropback — so
`snap_share × dropbacks` undercounts his routes and the rate comes out above
true TPRR. Jaxon Smith-Njigba's 2025 season gives 40.5% here against a real
TPRR in the high twenties. The ranking is roughly right; the level is not.
Do not put this number next to a PFF figure and expect them to agree.

A player with no recorded snaps returns an empty cell rather than a rate. The
alternative is a division by zero, or an enormous rate off a single snap.

### Receiving yards per game

Receiving yards divided by games with at least one offensive snap. Games a
player missed entirely do not drag his average down: a receiver who missed six
weeks is a full-time receiver who missed six weeks.

---

## Running back workload

Up to three backs per team, from `pipeline/metrics/rushing.py`.

- **Snap share** — Pro Football Reference `offense_pct`, via
  `load_snap_counts()`. Averaged over games the player appeared in, not over
  the season's weeks.
- **Rush attempts per game** — attempts divided by games with a snap.
- **Target share** — as above, and computed by the same code the passing table
  uses. A stat that appears in two tables has to mean the same thing in both.
- **Yards per attempt** — rushing yards divided by attempts.

Sorted on **snap share**, not carries: 14 carries in a blowout and 14 carries
in a one-score game are not the same workload, and the snap column is what says
so. A back with no snap-count match sorts last rather than first — an unknown
share is not a zero one, but it cannot outrank a measured one either.

**Who qualifies.** Running backs and fullbacks who carried the ball, at a rate
of at least one attempt per game (`RUSHER_MIN_ATT_PER_GAME`). Without that
floor the third row fills with blocking fullbacks: over 2025 it was Reggie
Gilliam and Kyle Juszczyk at a tenth of a carry a game. They are real players
with real snap shares, but "workload" is the column heading and theirs is not a
rushing workload. The floor is per game rather than a season total so it means
the same thing in week 2 as in week 12.

A quarterback can lead his team in carries, so the position filter runs before
the sort. And a team can legitimately have one qualifying back — San Francisco
in 2025 is Christian McCaffrey and nobody else.

### Snap counts are joined through the roster

PFR identifies players by its own id; the rest of nflverse uses gsis ids. The
map between them comes from that season's roster file. Over 2025's skill
positions it covers all but 24 of 6,294 rows, and the misses are deep reserves
who would never reach a five-row table. An unmatched player loses his snap
share and his TGT RATE, and keeps everything else.

---

## Home and away splits

From `pipeline/metrics/splits.py`, over `nflreadpy.load_player_stats()` weekly
rows joined to the schedule. Two tables — PPR for skill players, accuracy for
kickers — sharing one question: does this player travel?

### The window: the last 17 games

Every other module on the page reads a season. These two read a fixed trailing
count of games, per player, reaching back across the season boundary when this
season has not supplied enough.

The reason is arithmetic. A venue split halves whatever sample it is handed. On
the season rule, weeks 2 to 7 hold one to three games at each venue, and a
two-game home average is not a home average — the whole table would be dashes
for six weeks. Seventeen games keeps roughly eight or nine a side all year.

Counted **per player, not per team**: a back who missed six weeks is judged on
the last seventeen games he played, not on the seventeen his team played
without him. That is also why each cell carries its own count — see below.

The window is `SPLIT_TRAILING_GAMES` in `pipeline/config.py`, and the badge that
says so is written by `metrics/sample.py` like every other badge.

### Home and away

Which side was at home comes from the schedule feed, joined on `game_id`.
nflverse does encode it in the id itself — `2026_01_NE_SEA` ends with the home
team — but that is a naming convention, and the schedule is the feed that
actually knows.

Team codes go through `to_abbr()` on both sides of that join. An unmapped code
would not raise here; it would quietly mark every one of that team's home games
an away game, which is worse than a missing table because it looks like data.

### PPR per game

Full PPR exactly as nflverse scores it, in the `fantasy_points_ppr` column. We
publish that number rather than computing one, so the rules are theirs:

```
1.0  per reception
0.1  per rushing or receiving yard
1/25 per passing yard
4    per passing touchdown
6    per rushing or receiving touchdown
-2   per interception or lost fumble
```

**Four-point passing touchdowns**, which is worth stating because "PPR" is not
one thing and plenty of leagues use six. Averaged over games played, not weeks
elapsed — a player who missed six weeks is not a four-point receiver.

Worked example: Aaron Rodgers, 2025 week 4 — 244 passing yards, 4 touchdowns,
one rushing yard lost. 244/25 + 4x4 - 0.1 = 25.66, which is what the feed says.

### Home, Away, and Split

`Home` and `Away` are that average at each venue, printed with the games behind
it: `20.0 (8)`. The count is not decoration. The module badge quotes a 17-game
window, but a receiver in his second season has nine, and without the
parenthetical the two read identically.

`Split` is home minus away, signed. It is blank when either side is blank — a
split measured against a dash is not a split.

**Where a dash appears.** A venue with fewer than `SPLIT_MIN_GAMES_PER_SIDE`
games (three) shows `-- (2)` rather than an average: below that, one big
afternoon moves the number by more than the split it is supposed to measure.
The player keeps his row, because his overall PPR is still a real number.

`-- (2)` and a bare `--` are deliberately different states. The first is the
pipeline saying it had two games and would not average them; the second is no
data at all. Collapsing them would hide the difference between a thin sample
and a broken join.

### Who is listed

The quarterback, then the four highest scorers among RB, WR and TE.

The quarterback is **pinned rather than ranked**. On raw PPR a starting
quarterback outscores his own receivers on almost every team, so ranking him
would cost a skill-player row on all 32 and tell nobody anything. Which
quarterback: the one with the most appearances in the window, not the highest
scorer — a backup with two big afternoons is not the starter, and putting him
at the top of the table says he is.

A team with no qualifying quarterback simply has one row fewer. Row count is
`FANTASY_ROWS`.

As everywhere else, only players on the **active** roster appear, and week 1
lists this year's players with last year's numbers — see *Week 1 lists this
year's players with last year's numbers* below, which applies here unchanged.

---

## Kicking

From the same weekly rows, same window, same venue join.

### There is no points column, on purpose

nflverse scores every kicker **0.0** fantasy points. Its formula excludes
kicking outright — the only non-zero kicker-week in all of 2025 is Brandon
Aubrey's six rushing yards on a fake, and the four field goals he made that
afternoon scored him nothing.

So any points figure here would be a scoring rule we invented. There is no
single convention: 3/4/5 by distance is common, flat 3 is common, and whether a
miss costs a point depends on the league. Rather than publish an editorial
choice as though it were a fact, this table reports what is not in dispute —
made, attempted, long, and volume. A live test in `test_live_feeds.py` holds
the nflverse half of that claim, so if it ever changes we revisit the decision
instead of quietly leaving the column off.

### FG, FG%, Long, Att/gm

- **FG** — made over attempted at that venue, e.g. `13/14`. Blocked attempts
  count as attempts, the way every kicking table counts them.
- **FG%** — made divided by attempted. A fraction in the payload, a percentage
  on the page, like every other rate here.
- **Long** — the longest field goal *made* at that venue during the window.
- **Att/gm** — attempts divided by games played at that venue. Volume is the
  half of a kicker that his offense controls rather than his leg.

### Who qualifies

One kicker per team: the one with the most appearances in the window. A team
carries one, and listing the man he replaced in October would read as a
competition that is not happening.

He needs `KICKER_MIN_FG_ATT` (five) attempts across the whole window before he
appears at all. A kicker signed in December has gone 2-for-2 somewhere, and
100% off two kicks is not a hundred percent of anything.

**A team can legitimately have no kicking table.** A rookie kicker has no NFL
history to split — on the 2026 opening weekend that is Green Bay, the Giants and
Washington, all three starting a kicker with no prior-season games. This is the
one table where an empty side is a real outcome rather than a failed join, and
it is the reason kickers are excluded from the build's missing-team warning.

### The weather line

The kicking module repeats the game's weather summary under its heading. It is
the reason the table exists — a dome, an altitude, and a crosswind are exactly
what a venue split measures — and it is otherwise twenty rows further up the
page.

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
| any | Trailing 17 games (split tables only) | `last 17 games` |

The cutovers are `PRIOR_SEASON_THROUGH_WEEK` and `SMALL_SAMPLE_THROUGH_WEEK` in
`pipeline/config.py` — one constant each, not logic scattered across modules.
`config.stats_season()` picks the season and `metrics/sample.py` writes the
badge; the badge text lives in exactly one place.

Where the two teams differ — after a bye, in weeks 2 to 4 — the badge quotes
the **fewer** of the two. It is a claim about the table, and the table holds
both teams, so it has to describe the thinner half.

The fourth basis belongs to the home and away split tables alone, and it does
not vary by week. They read a fixed window rather than a season because
splitting by venue halves whatever sample it is given; the reasoning is in
*Home and away splits* above. Their badge describes the window, so what each
individual row rests on travels with the row as its own game counts.

That is also why the admin screen's "stats basis" readout ignores those two
modules: it answers "what is the whole screen resting on", and `last 17 games`
would be a wrong answer to that question.

Odds, weather, and injuries are exempt. They are always current.

### Week 1 lists this year's players with last year's numbers

This is the decision most likely to be questioned, so it is written down rather
than left to the code.

In week 1 every player table is built from last season's production, and
between February and September a third of the league changes address. Two bad
options and one chosen one:

- Attribute production to the **team it was earned with**, and Seattle's
  receiver table lists players who left in free agency.
- Attribute it to the player's **current team**, and his target share is a
  share of a team he no longer plays for.

**We do the second, and say so.** The player is listed under the jersey he will
actually wear on Sunday; his target share is still a share of the team he
earned it with; and the `2025 season` badge on the module says where the
numbers came from. The table answers what a reader is actually asking — *who
are these guys, and what did they do last year*.

The acknowledged cost: a receiver's share describes an offense that is not the
one he is now in. From week 2 the two teams are the same and the question
disappears for another year.

Only players on the **active** roster appear. Cut, waived, practice-squad, PUP,
and reserve-list players are dropped — a player on PUP misses the first four
games, and the injury table is where he belongs. In 2026 this correctly moves
Kenneth Walker III to Kansas City, brings Emanuel Wilson into Seattle's
backfield, and drops Zach Charbonnet, who is on PUP.

Team efficiency is unaffected. It is a team-level number, so 2025 Seattle is
simply 2025 Seattle.

### One nflverse inconsistency worth knowing about

The roster file codes Arizona **`AZ`**. Schedules, play-by-play, snap counts,
and player stats all say **`ARI`**. Since players are keyed to their current
team through the roster, the mismatch silently published two empty tables for
Arizona and nothing else went wrong — no error, no warning, just a team with no
players. It is handled by an alias in `sources/team_map.py`, and the build now
names any team that ends up with an empty table while others filled.
