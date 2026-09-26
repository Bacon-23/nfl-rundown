# DvP (Defense vs. Position) module

## Context

DvP was the one mockup module deferred out of V1 (`docs/plan.md:32`, Phase 5 at
`:364`). The groundwork is already there: `Game.dvp: dict | None` is reserved in
`pipeline/schema.py:269`, and `'dvp'` is already in `STICKY_KEYS` in
`wordpress/trinity-rundown/includes/storage.php:79`.

The mockup's version had hand-written ranks (a different stat on every row), a
trend word and a free-text note. None of that can be computed honestly. Carter
supplied reference screenshots (kept outside the repo in
`C:\Users\cg_ri\Projects\rundown-ideas\dvp\`, because they come from another
site), and the design follows them instead.

**Decisions made in this session:**
- **Scope:** both halves of the reference.
  - An **"X allows" table** by role, where every stat carries a 1–32 rank.
  - The **offense's player lines**, with each cell coloured by what the
    defense allows to that player's role.
- **Placement:** one new **DvP** tab with **Passing / Receiving / Rushing**
  sections.
- **Columns:** the reference's columns plus **PPR allowed**.
- **Dropped:** the mockup's trend word and NOTE column (the pipeline never
  writes editorial fields; Scouting Notes covers commentary). Also dropped from
  the reference: the Value/Rank sort toggle, the WR1/WR2 "Advanced" split and
  the paywall.

**Cost:** no new feed and no Odds API credits. Everything comes from nflverse
weekly player stats, which are already loaded and carry `opponent_team` (checked
live against 2025), plus play-by-play, which is also already loaded.

## Definitions (these go into `docs/metrics.md`)

- **Window:** the same as the other season modules. `config.stats_season()`
  and `sample.describe()` handle it: week 1 uses the prior season, weeks 2–4
  carry the `n = X games` badge. Regular season only.
- **Roles:** QB, WR, TE, RB. **FB counts as RB**, as in the reference. The
  position comes from the weekly stats' `position`.
- **Allowed per game:** for defense D and role R, the sum of stat S across
  every opponent player at R in games against D, divided by D's games played.
  All opponents are combined ("All WRs").
- **Rank:** 1 to 32, where **1 = gives up the most**. **INT is inverted**
  (1 = fewest INTs), so rank 1 always favours the offense. Ties break on the
  team abbreviation, the same rule as PROE and EPA.
- **Tiers:** 1–11 favourable, 12–22 neutral, 23–32 tough. The rank number is
  always printed beside the colour, so the tier never relies on colour alone.
- **Section columns:**
  - **Passing (QB):** pass yds, comp, att, pass TD, INT, PPR.
  - **Receiving (WR, TE, RB):** rec, rec yds, rec TD, RZ tgts, long rec, PPR.
  - **Rushing (QB, RB):** carries, rush yds, rush TD, RZ carries, long rush,
    PPR.
- **PPR** is the role's **total** `fantasy_points_ppr` allowed, from nflverse
  and not recomputed. The RB value is therefore the same number in Receiving
  and Rushing, and its tooltip says so.
- **Where each stat comes from:**
  - Box-score columns: weekly `passing_yards`, `completions`, `attempts`,
    `passing_tds`, `passing_interceptions`, `receptions`, `receiving_yards`,
    `receiving_tds`, `carries`, `rushing_yards`, `rushing_tds`.
  - **RZ tgts / RZ carries and long rec / long rush:** play-by-play. RZ uses
    `RED_ZONE_YARDS` and the same target and designed-run filters as
    `pbp.scoring_usage()`.
  - **Long** = the per-game longest, averaged over games. That's what the
    Longest Reception/Rush prop prices. It is stated in the footnote because
    the reference uses season maxima.
- **Player lines:** active-roster players only (`snaps.current_teams`), each
  showing his own per-game line over games played.
  - **Passing:** the starting QB, meaning the one with the most appearances
    (same rule as `splits._pin_quarterback`).
  - **Receiving:** WR/TE/RB/FB by targets per game, capped at
    `DVP_RECEIVING_ROWS` (8).
  - **Rushing:** the QB, plus RB/FB above `RUSHER_MIN_ATT_PER_GAME`, capped at
    `DVP_RUSHING_ROWS` (4).

## Implementation

1. **Spec:** commit this design as
   `docs/superpowers/specs/2026-09-26-dvp-design.md`, following the precedent
   of the cutover spec.
2. **Schema** (`pipeline/schema.py`): replace `dvp: dict | None` with typed
   models. The slot has never been written, so this breaks nothing that's
   stored.
   - `DvpCell { value: float|None, rank: int|None }`
   - `DvpRoleRow { role, stats: dict[str, DvpCell] }`
   - `DvpPlayerRow { player, role, position, games, stats: dict[str, float|None] }`
   - `DvpSection { allows: list[DvpRoleRow], players: list[DvpPlayerRow] }`
   - `DvpSide { passing, receiving, rushing: DvpSection | None }`
   - `DvpModule(Module) { away: DvpSide|None, home: DvpSide|None }`: `away`
     is the away offense against the home defense.
   - The column keys are constants shared with PHP, because the dot-path
     contract applies here too.
3. **Source columns:**
   - Add the new weekly columns plus `opponent_team` to a separate
     `DVP_COLUMNS` set in `pipeline/sources/players.py`. It stays separate
     from `REQUIRED_COLUMNS` so that drift in these columns darkens only DvP,
     mirroring `pbp.SCORING_COLUMNS`.
   - Add the pbp columns for longs and RZ (`yards_gained`, `passer`/`rusher`/
     `receiver` ids, `defteam`) to a new `DVP_COLUMNS` in
     `pipeline/sources/pbp.py`.
4. **Metric** (`pipeline/metrics/dvp.py`, new):
   - `allowed(weekly, pbp) -> dict[defteam, dict[role, dict[stat, float]]]`
   - `rank(allowed) -> ranks`, which inverts INT.
   - `player_lines(weekly, pbp, roster_season) -> dict[team, sections]`
   - `build(stats_season, roster_season) -> dict[team, DvpSide inputs]`
   - Reuse `pbp.load`, `players.load`, `snaps.current_teams`, `team_map.to_abbr`,
     `pbp.check_columns` and the `_round` helper from `metrics/passing.py`.
5. **Wiring** (`pipeline/build_week.py::_attach_stats`): one more independently
   guarded `try` block, the same `basis`/`badge` as passing, `game.dvp =
   DvpModule(...)`, and `("dvp", dvp)` added to `_missing_team_warnings`.
6. **Config** (`pipeline/config.py`): `DVP_RECEIVING_ROWS`, `DVP_RUSHING_ROWS`
   and `DVP_TIERS = (11, 22)`.
7. **Render** (`wordpress/trinity-rundown/includes/render.php`):
   - Add a `'dvp' => ['label' => 'DvP', ...]` entry in `trun_game_tabs()` after
     Rushing.
   - Add `trun_render_dvp()`, which renders three stacked sections. Each has
     two side-by-side blocks ("NE offense against JAC defense"), and each
     block has an "allows" table plus a players table.
   - Cells render as value + rank chip, with the tier class looked up from the
     allows row for that player's role.
   - Reuse `trun_render_stat_table`, `trun_module_sides`, `trun_render_badge`,
     `trun_abbr` (column tooltips) and `trun_decimal`.
   - A legend and a one-paragraph footnote carry the definitions.
   - Build from the "View Advanced" pieces only what's needed; no sort toggle.
8. **CSS** (`assets/rundown.css`): `.trundown-dvp-*` tier classes (tints on the
   dark theme, contrast-checked), the rank chip, and the existing <640 px card
   reflow applied to the new tables. **No JS changes**: the tab system already
   handles a new tab.
9. **Docs:** a DvP section in `docs/metrics.md`, and mark DvP done in the
   `docs/plan.md` Phase 5 list.

## Verification

- **Unit tests** (`pipeline/tests/test_dvp.py`, synthetic frames in the style
  of `test_splits.py`):
  - allowed per game divides by the *defense's* games;
  - FB counts as RB;
  - rank 1 = most allowed, with INT inverted;
  - ties break on the abbreviation;
  - long is the mean of per-game longest;
  - RZ matches the `scoring_usage` filters;
  - an off-roster player is dropped;
  - the QB is pinned by appearances;
  - Arizona joins despite `AZ`.
- **`test_build_stats.py`:** a DvP failure warns and leaves the other modules
  intact, and a game gets `dvp` with the right away/home orientation.
- **`test_live_feeds.py`:** the new weekly and pbp columns exist upstream.
- **Sanity check:** run `python -m pipeline.build_week` locally for the
  current week, and spot-check one defense's numbers by hand against the raw
  2026 weekly rows. Totals over the 32 defenses should equal league totals.
- **PHP:**
  - `php -l` and `phpcs` via Docker (see the local-PHP memory; watch the CRLF
    trap).
  - Render a real payload through `tools/preview.php`, then check desktop and
    mobile widths in the browser, in the dark theme.
- **Staging first:** merge to `main` (staging auto-deploys), cache-bust, run
  the build workflow against staging and review the DvP tab. **Production
  plugin deploy is held until Carter signs off.**

## As built — where it departed from the plan above

- **Tier bounds live in PHP, not `config.py`.** The pipeline never reads
  them — it ships ranks, and the band is presentation — so a Python
  `DVP_TIERS` would have been dead code. They are in `trun_dvp_tier()`.
- **The shared table renderer gained one capability.** A column's `cell` may
  return `['text', 'aside', 'class']` instead of a string (`trun_cell_html()`),
  still escaped in one place. That is how the rank chip, the tier tint and
  the position beside a player's name reach the page without any callback
  emitting raw HTML.
- **The admin screen shows the DvP tables too**, in the same collapsed stats
  block as the others (`trun_admin_render_stats()`).
- **Receiving widens its name column** (and narrows its stat columns to 11%)
  so names do not wrap onto three lines; the Role column widens to match, so
  the stat columns still line up between the two tables.
- **Checked against the reference screenshots** (2025, JAX and NE): box-score
  figures agree to within a few percent and ranks within a place or two. The
  one intended divergence is Long — theirs is a season maximum (JAX vs RBs:
  38.0, which our data reproduces), ours the per-game longest averaged (14.9).
- **No DvP tab any more (0.10.1).** The sub-tab strip (#31) was dropped at
  Carter's request: each section now renders as its own module,
  `trun_render_dvp_section( $game, $key )`, at the top of the game tab of the
  same name -- DvP Passing above the quarterback tables, Receiving above the
  receivers, Rushing above the running-back workload. The payload is
  unchanged.
