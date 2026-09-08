# The NFL Rundown

Weekly NFL matchup dashboards for Trinity Analytics. A Python pipeline computes
every number from public data plus The Odds API and pushes it to WordPress; a
plugin gives the writer one screen to add commentary and publish.

Full design: [`docs/plan.md`](docs/plan.md). Metric definitions:
[`docs/metrics.md`](docs/metrics.md).

## How it fits together

```
GitHub Actions (hourly, Tue-Sun)
        |
        |  build_week.py  — nflverse + Odds API + ESPN + Open-Meteo
        v
  week payload (JSON)
        |
        |  POST /wp-json/trinity-rundown/v1/week   (bearer token)
        v
WordPress  ->  wp_trinity_rundown_games  ->  [rundown_week] shortcode
                        ^
                        |
              writer adds notes in wp-admin, hits Publish, numbers freeze
```

The one rule that shapes everything: **the pipeline writes `stats_json`, humans
write `notes_json` and `overrides_json`, and neither can overwrite the other.**
A refresh can run mid-edit without eating a paragraph.

## Layout

| Path | What it is |
|---|---|
| `pipeline/sources/` | One module per external feed. Each returns plain data, no formatting. |
| `pipeline/metrics/` | Stat computation over nflverse play-by-play. |
| `pipeline/schema.py` | The payload contract. Renaming a field here is a breaking change. |
| `wordpress/trinity-rundown/` | The plugin. Auto-deploys via WordPress.com GitHub Deployments. |
| `tools/preview.php` | Renders the front end from a payload, with no WordPress. |

## Running the pipeline

```bash
python -m venv .venv && ./.venv/Scripts/pip install -e ".[dev]"   # Windows
python -m venv .venv && ./.venv/bin/pip install -e ".[dev]"       # macOS/Linux

# Build without touching WordPress or spending API credits
python -m pipeline.build_week --season 2026 --week 1 --no-odds-api --dry-run

# Build from the recorded odds fixture — no credits, identical every run
python -m pipeline.build_week --season 2026 --week 1 --replay-odds --dry-run

# Build with live odds, still without pushing
python -m pipeline.build_week --season 2026 --week 1 --dry-run

# The real thing
python -m pipeline.build_week --season 2026 --week auto --push
```

`--week auto` resolves to whichever week holds the next kickoff, and stays on a
week until its last game finishes — a Monday-nighter does not flip the build
to next week while it is still being played.

### Environment variables

| Variable | Needed for | Notes |
|---|---|---|
| `ODDS_API_KEY` | live odds | Without it the build falls back to nflverse lines and warns. Not needed when replaying. |
| `WP_SITE_URL` | `--push` | e.g. `https://example.com`, no trailing slash. |
| `TRINITY_RUNDOWN_TOKEN` | `--push` | Must match the constant in that site's `wp-config.php`. |
| `ODDS_BOOK` | optional | Defaults to `draftkings`. |

## Two environments

Staging and production are separate WordPress sites with separate databases and
**separate tokens**. One token per site is deliberate: a mistyped `WP_SITE_URL`
then fails loudly instead of quietly writing to the wrong database.

| | Staging | Production |
|---|---|---|
| Plugin deploy | GitHub Deployments, automatic on push to `main` | GitHub Deployments, manual |
| Scheduled builds | yes — the cron target | none until launch |
| Odds | replayed from a committed fixture | live Odds API |
| Secrets | GitHub Environment `staging` | GitHub Environment `production` |

The hourly schedule is held behind a repo variable: the `build` job runs on a
`schedule` trigger only when **`CRON_ENABLED`** is `true` (Settings → Secrets
and variables → Actions → Variables). It starts unset, because the schedule
would otherwise begin firing the moment the workflow reached `main` — before
the odds fixture existed or staging had a token. Manual `workflow_dispatch`
runs ignore the variable, so setup can be tested throughout. Turn it on once
`python -m pipeline.push --health` returns `ok: True` from CI and the fixture
is committed.

`WP_SITE_URL` and `TRINITY_RUNDOWN_TOKEN` live in **GitHub Environments**, not
repo-level secrets, so a job only ever holds the credential for the site it
declares. `ODDS_API_KEY` is repo-level, since one subscription serves both.

> **Do not use WordPress.com's "Push to Production" sync.** Its dialog offers to
> copy the database, which would overwrite live posts with staging content.
> Plugin code reaches each site from git, independently.

### Recording the odds fixture

Staging replays a captured API response so test runs cost nothing and return
the same numbers every time:

Record it from CI rather than a workstation, so the API key stays in Actions
secrets: dispatch **Build week** with `odds: record` and `push: false`, then
download the `odds-fixture` artifact and commit it to `pipeline/fixtures/`.

The equivalent locally, if the key is already in your environment:

```bash
python -m pipeline.build_week --season 2026 --week 1 --record-odds --dry-run

# Thereafter -- no key needed, no credits spent
python -m pipeline.build_week --season 2026 --week 1 --replay-odds --dry-run
```

Recording costs roughly **19 credits** for a 16-game week, not the 3 a reading
of the bulk endpoint suggests: 3 for the featured markets in one bulk call,
plus one per game for `team_totals`, which is only available per event. A
recording always pays for the per-event calls, whatever the probe schedule
below says — a fixture that skipped them would silently lose team totals on
every replay taken from it.

### The team-totals probe

A normal live build costs **3 credits**, not 19. `team_totals` is a per-event
market at a credit a game, and on the 2026 Week 1 slate DraftKings posted it
for none of them: all sixteen responses came back `200` with an empty
`bookmakers` list, and every team total was derived from the spread and total
regardless. Paying that on an hourly cron is ~10,000 credits a month for
nothing, on a subscription shared with other products.

So a live build probes the market on one hour a day —
`ODDS_TEAM_TOTALS_PROBE_HOUR`, 12:00 UTC — and derives the rest of the time.
Set it to `None` to stop probing at all.

Little is lost by the lag. A derived team total tracks the spread and total as
they move, where a posted one from this morning would not, so the derived
figure is arguably the fresher of the two. What the daily probe buys is
noticing: if the book starts posting the market mid-season, a build picks it up
within a day rather than never, and logs how many games it found.

Recording and replaying ignore the schedule entirely — see
`_should_probe_team_totals`. A replay whose output depended on the hour it ran
at would destroy the property the fixture exists for.

Fixtures live in `pipeline/fixtures/` and never contain the API key.

Replay joins events to games by team pair, exactly as the live path does. So a
Week 1 fixture replayed in Week 5 would match nothing, every game would fall
back to nflverse lines, and a broken parser would still look green. Replay
therefore reports its match rate and **fails below 50%**:

```
Odds fixture problem: Odds fixture matched only 0/16 games
(recorded 2026-08-24). It is stale -- re-record with --record-odds.
```

Re-record when the week rolls over.

## WordPress setup

Do all of this on **staging** first. Production repeats the same steps at
launch with its own token.

1. **Enable SSH** on the staging site (WordPress.com → Hosting → Overview).
   That gives WP-CLI, which is both the debugging tool and the fallback ingest
   path.
2. **Generate a token** — `openssl rand -hex 32` — and add it to that site's
   `wp-config.php` over SFTP, above the "stop editing" line:

   ```php
   define( 'TRINITY_RUNDOWN_TOKEN', '<the generated string>' );
   ```

3. Connect WordPress.com GitHub Deployments to this repo, in **advanced
   mode**, with destination `/wp-content/plugins/trinity-rundown`. Staging
   deploys automatically from `main`; production stays manual.

   Advanced mode is not optional here. There is no source-directory field
   anywhere in the UI: *simple* mode copies the whole branch to the
   destination, which lands the repo root at
   `wp-content/plugins/trinity-rundown` and leaves the plugin one level too
   deep for WordPress to detect. Advanced mode deploys the contents of the
   artifact named `wpcom` instead, which is what
   `.github/workflows/wpcom.yml` builds from `wordpress/trinity-rundown`.

   Two things about connecting that are easy to be surprised by:

   - **Connecting writes a commit to `main`.** WordPress.com generates
     `.github/workflows/wpcom.yml` itself and pushes it, overwriting the
     committed one. Its default uploads the entire repository as the
     artifact. After connecting, check that the `path:` still reads
     `wordpress/trinity-rundown` and restore it if not. On production this
     commit lands on `main`, which auto-deploys staging.
   - **Deploys merge, they do not replace.** Files from a previous deploy
     survive in the destination. After fixing a bad deploy, delete the
     directory on the server before redeploying.
4. Activate **Trinity Rundown**. The table is created on activation, and on any
   version bump thereafter (GitHub Deployments overwrites files without
   reactivating, so the plugin re-checks on load).
5. **Probe reachability from outside** — this is the step that decides whether
   the REST push works at all, since a WordPress.com staging site's response to
   anonymous requests is not documented:

   ```bash
   python -m pipeline.push --health
   ```

   Run it from CI rather than a laptop; the question is specifically whether
   GitHub Actions can reach the site. Expect `ok: True`. A 503 means the
   constant is missing, a 404 means the plugin is not active, and a hang or
   redirect to a login means the site is gated — in which case switch to the
   SSH transport, which sends the same payload through `wp rundown seed`.

   **Answered for staging on 2026-09-02: the site does answer anonymous
   requests**, and the probe returned `ok: True` from CI. The REST transport
   works as designed and the SSH fallback was never needed. Production is a
   different site and inherits nothing from this — re-run the probe there.

6. Put `[rundown_week season="2026" week="1"]` in the weekly post.

### WP-CLI

```bash
wp rundown seed --file=build/2026-week-01.json   # load a payload with no pipeline
wp rundown status --season=2026 --week=1         # what is stored, locked, noted
wp rundown publish --season=2026 --week=1        # freeze the numbers
wp rundown unlock --season=2026 --week=1         # let refreshes through again
```

## The writer's screen

**Rundown** in the wp-admin sidebar (capability `edit_posts`). One page, every
game in the week, picked with the dropdown at the top.

Per game it shows what the pipeline currently says -- spread, total, both team
totals, weather, injury count -- and warns about anything the writer should
know before publishing a number: lines that came from the nflverse fallback
rather than the book, team totals derived from spread and total rather than
posted, and a game that has never had injury data stored.

Three boxes per game write `notes_json`: **Scouting Notes**, **Anytime TD
Leans**, **Score Prediction**. Under **Corrections** are per-field overrides
(`overrides_json`) for the odds and weather values. Each override box shows the
pipeline's value as its placeholder, so an empty box visibly means "use the
pipeline's number" -- and **clearing a box removes the correction** rather than
blanking the field. That is why overrides are rebuilt from the form on every
save instead of merged into what was stored before.

**Save all games** writes both human columns for all 16 games at once. It never
touches `stats_json`, so a pipeline run mid-edit cannot eat a paragraph.

**Publish & freeze** copies the merged view into `published_json` and sets
`locked = 1` -- the same thing `wp rundown publish` does. It does *not* create
the weekly post: make the post yourself and paste in `[rundown_week]`. The
plugin has no post-creation powers anywhere, admin screen included.

While a week is locked the pipeline keeps updating `stats_json` in the
background, so the live and published views drift apart silently. The screen
says so per game -- "the pipeline has moved since this week was published" --
and **Unlock** puts the page back on the live data.

## Tests

```bash
pytest -q
```

The odds suite runs against recorded HTTP fixtures, so it needs no API key and
spends no credits. It covers the paths that matter when something is wrong:
quota exhaustion, a rejected key, a network failure, a game the book has not
posted, a team name that cannot be mapped (a hard error — a silently dropped
game would publish a matchup with a blank line), and a stale replay fixture.

PHP has no local toolchain requirement -- CI lints it on every push. To run
the same two gates before pushing, in Docker:

```bash
# Syntax, on the version CI uses
docker run --rm -v "$PWD/wordpress:/src:ro" php:8.2-cli   sh -c "find /src -name '*.php' -print0 | xargs -0 -n1 -P4 php -l"

# WordPress coding standards, via the committed ruleset
docker run --rm -v "$PWD:/repo:ro" -w /repo composer:2 sh -c   'composer global config --no-plugins allow-plugins.dealerdirect/phpcodesniffer-composer-installer true &&    composer global require --quiet --no-interaction squizlabs/php_codesniffer      wp-coding-standards/wpcs dealerdirect/phpcodesniffer-composer-installer &&    $(composer global config bin-dir --absolute --quiet)/phpcs --standard=phpcs.xml.dist'
```

`phpcs.xml.dist` is WordPress-Core with two sniffs excluded: short array
syntax, and class-file naming. Both fight conventions the plugin already
applies consistently, and neither affects what runs.

## Seeing the page without deploying

`tools/preview.php` renders a `build/*.json` payload to a standalone HTML file
using the real `storage.php` and `render.php`. It fakes the database, not the
code: rows are built by hand and passed through `TRUN_Storage::view_row()`, so
the stats/overrides/notes merge runs exactly as it does on the site.

```bash
python -m pipeline.build_week --season 2026 --week 1 --replay-odds --dry-run

MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src" -w /src php:8.2-cli \
  php tools/preview.php build/2026-week-01.json build/preview.html --notes
```

Open `build/preview.html`. It *links* the real stylesheet rather than inlining
it, so a CSS edit needs only a reload.

| Flag | What it does |
|---|---|
| `--notes` | attach sample editorial copy, so the writer's sections render |
| `--game=NE_SEA` | render one matchup, for faster iteration |
| `--locked` | render as a published week, out of `published_json` |

What it cannot tell you: the theme's own typography and colors, which only
staging has. Judge markup, layout and reflow here; judge color there.

## Current state

Landed: schedule, venue, odds with nflverse fallback, opening-line capture,
record/replay, auto week detection, injuries, weather, ATS/over-under records,
the plugin's storage and render layers, and the two-environment workflow.
105 tests passing.

Verified since: all six plugin files pass `php -l` on PHP 8.2 and are clean
under the WordPress-Core ruleset, both locally in Docker and in CI. The nine
`WordPress.DB.PreparedSQL` findings were false positives -- every value is
already parameterised and only the table name is interpolated, which
`prepare()` cannot substitute -- and are suppressed with a pointer to the
explanation on `TRUN_Storage::table()`.

**The plugin now runs.** On 2026-09-02 it was deployed to the staging site and
activated: `TRUN_Storage::install()` created `wp_trinity_rundown_games`, the
health probe returned `ok: True` from CI, and a `--no-odds-api` build pushed
2026 week 1 over REST -- 16 inserted, 16 openers recorded. Anonymous requests
reach the site, so the SSH transport was never needed.

Staging paths, for later WP-CLI work: the site root is `/srv/htdocs`, which is
**not** the SSH home directory (`/home/<id>`). Plugins live at
`/srv/htdocs/wp-content/plugins/`.

The week 1 odds fixture is recorded and replays 16/16, and the hourly staging
cron is live as of 2026-09-02. The stats/editorial separation is verified
against it: a note written into `notes_json` survived two unattended scheduled
runs byte-identical.

**The hourly schedule is best-effort.** Measured over the first four hours,
GitHub delivered two of four scheduled runs, 31 and 47 minutes late. Nothing
breaks -- the build is idempotent -- but do not read "hourly" as a guarantee.
In particular, `opening_line` is captured by the first run of the week that
actually executes, which may be well after the intended Tuesday slot; it is
write-once, so it cannot be corrupted, only later than expected.

**The writer can now write.** Phase 3 landed 2026-09-03: `admin-week.php` puts
all 16 games on one screen with notes, per-field corrections, and publish /
unlock. It reuses `render.php`'s dot-path helpers rather than growing a second
set, and `storage.php` gained one method -- `list_weeks()` -- to drive the week
picker. All seven plugin files pass `php -l` on PHP 8.2 and the WordPress-Core
ruleset.

Walked end to end on staging the same day, through the UI rather than through a
seeded payload:

- Notes written on one game survived a dispatched pipeline run **byte-identical**
  -- `MD5(notes_json)` unchanged while `updated_at` moved on all 16 rows, so the
  run genuinely rewrote every row and still could not reach the editorial column.
  This is the invariant the whole two-column design exists for.
- A correction applied and then **cleared** restored the pipeline's value, which
  is the path a stored empty string would have silently blanked.
- Freeze holds: with the week locked, a correction saved in wp-admin did not
  reach the page, and appeared the moment the week was unlocked. The published
  page is genuinely served from `published_json`.

The first front-end render also happened here. `[rundown_week]` had never been
placed in a post before 2026-09-03, so nothing readers see had ever been
exercised -- worth remembering when reading earlier "verified" notes, which all
covered the pipeline and the database rather than the page.

**The stat modules landed 2026-09-03.** Phase 2 added `sources/pbp.py`,
`snaps.py`, and `players.py`, and `metrics/efficiency.py`, `passing.py`,
`rushing.py`, and `sample.py` — team efficiency, the passing table, the
running-back table, and the sample badge that says which season a number came
from. They attach in `build_week._attach_stats()`, which computes each table
league-wide once per build and slices it per game, and each of the three fails
independently: a dead snap-count feed costs the backfield table and nothing
else. 183 tests pass.

Two things that only showed up against real data:

- **nflverse codes Arizona `AZ` in the roster file and `ARI` everywhere else.**
  Players are keyed to their current team through the roster, so Arizona
  published two empty tables and raised nothing at all. Fixed with an alias in
  `team_map.py`, and the build now names any team whose table comes back empty
  while other teams' fill — the general version of the bug, not just this one.
- **The third running-back row was mostly fullbacks.** Reggie Gilliam and Kyle
  Juszczyk at a tenth of a carry a game are real players with real snap shares,
  but the column heading is "workload". `RUSHER_MIN_ATT_PER_GAME` now requires
  one carry a game to appear.

Three numbers were hand-checked against the raw feeds before any of this was
called done — Jaxon Smith-Njigba's 2025 target share, receiving yards per game,
and TGT RATE all reproduce exactly. Note that **TGT RATE reads high against
published TPRR** (40.5% vs high-twenties for JSMN): snap share counts running
plays, so the estimated-routes denominator is too small. The ranking is sound;
the level is not comparable to a PFF figure. `docs/metrics.md` says so, and the
column tooltip says so on the page.

**The schema-drift canaries were themselves broken.** `test_live_feeds.py`
holds the only tests that hit nflverse for real; they are opt-in
(`pytest -m live`) so a bad afternoon at GitHub never reddens CI, which also
means nobody had run them. The snap-count row floor turned out to be written
against the raw regular-season count (~25k) rather than the frame `load()`
returns after dropping everyone without an offensive snap (~10k), so it could
not have passed at any point. Corrected to 9,000 against four seasons that run
9,985 to 10,094 -- about 18.5 offensive players a team-game. All six now pass,
which is also the standing evidence that the 2025 feeds are intact: 18 weeks,
272 games, 32 teams.

**PHP now runs locally.** Docker is available on the dev machine after all, so
`php -l` and a small harness that stubs the dozen WordPress functions
`render.php` touches will render the modules against a real payload — see
`docs/metrics.md` for what they mean. All seven plugin files parse, and the
generated markup is well-formed with `<th scope>` throughout. That is a local
check, not a staging one: nothing here has been deployed yet.

**Phase 2 shipped 2026-09-08**, the day before kickoff, after the four local
gates: 189 tests (183 offline, 6 live), `php -l` on all seven plugin files,
phpcs on the committed ruleset, and a CRLF byte check.

Week 1 is deliberately **not** the launch. It is the end-to-end rehearsal this
plan always called for, run on staging against live Week 1 data -- which routes
through the prior-season fallback and so exercises the same path a synthetic
2025 run would. Production launches Week 2.

**Phase 4 landed 2026-09-08.** The page now looks like the mockup rather than
like a structural placeholder, and two things it had never shown are on it.

*The header the payload was already carrying.* `ats_record`, `ou_record`,
`moneyline` and `logo` have been in every payload since Phase 1, and the front
end rendered none of them -- a reader met a stat table without being told whose
it was. There is now a team bar (logo, name, straight-up record, moneyline) and
a season ATS / over-under strip. The straight-up record is new in the pipeline:
`records.py` tallies it in the same pass as the other two, outside both line
guards, because a game the book never posted still had a winner.

*The team-color collision, in general rather than as one pair.* `docs/plan.md`
named New England and Seattle sharing `#002244`. Checked against
`load_teams()`, **four** current teams are that navy -- Dallas, Denver, New
England, Seattle -- with Atlanta and Tampa Bay both `#A71930` and Las Vegas and
Pittsburgh both `#000000`. Since `2026_01_NE_SEA` sorts first, the panel that
renders expanded was the one with no accent at all. The renderer now falls the
*away* side back to `team_color2` on any collision, keeping the home color
fixed so the summary's left rule does not shift; a missing or equal secondary
falls through to the neutral rather than emitting a color. Verified: zero
collisions across all 16 Week 1 panels.

Text over a team color is no longer guessed at. `trun_ink_for()` picks black or
white by relative luminance, crossing over at 0.1791 where contrast against
white and against black are equal -- Pittsburgh's gold and the Rams' yellow
need black, and eyeballing it is how a header ends up at 3:1.

Two smaller things that only showed up on the rendered page:

- **The odds bar drew its cell separators as background bleeding through a 1px
  grid gap**, so six cells in an auto-fit five-column grid left the sixth alone
  beside four columns of bare grey. Column counts are now fixed to divide the
  cell counts exactly.
- **The injury table's phone reflow sized its columns per row**, since each
  `<tr>` is its own grid -- so every player's name landed somewhere different
  down the list. It uses named grid areas now.

The three stat tables stop scrolling sideways on a phone and stack into cards
instead, each cell printing its own heading from a `data-label` the renderer
emits. Every column heading carries its definition from `docs/metrics.md` as a
tooltip, not just PROE and TGT RATE. Known limit, stated in the CSS rather than
papered over: `title` does not open on touch.

`tools/preview.php` is how all of that was checked -- see above. It is the
first committed way to see the page without a deploy; the harness used in
Phase 2 was ad hoc and lost.

Gates at Phase 4: 195 offline tests and 8 live, `php -l` on all eight PHP
files, phpcs clean on the committed ruleset, and no CRLF. The rendered page was
checked at 900px and 375px, in print, and with JavaScript disabled, and against
a payload stripped of the new fields -- a week frozen into `published_json`
before this change renders dashes and the neutral accent rather than erroring.

**Deployed to staging the same day, and it found two bugs the local harness
could not.** The theme is dark, and dark *unconditionally* -- not by OS
preference -- and everything above had been checked against a white shell.

- The odds bar and records strip set `background: Canvas` on each cell, using
  the grid's background through a 1px gap as the separator. `Canvas` is the
  user agent's canvas colour, white whatever the theme, so the theme's
  near-white text sat on white boxes at a measured **1.21:1**: the spread, the
  total, both team totals and all four records were invisible on the live page.
  Separators are borders on the cells' start edges now, so a cell paints no
  background and keeps the theme's. 17.31:1 after.
- The status colours had lighter variants behind `prefers-color-scheme: dark`,
  which never fired, because the OS is not what makes this page dark. Out and
  Injured Reserve measured 3.21:1 and Questionable 3.05:1, both under AA. Each
  hue is now mixed 65% toward `currentColor` -- the idiom `--trun-border`,
  `--trun-muted` and `--trun-sunk` already used -- which puts them at 5.68,
  7.06 and 6.16 here and above 9 on a light page. The `prefers-color-scheme`
  block is gone rather than retuned: keying page colour to the OS was the bug.

`tools/preview.php` gained `--dark` in the same change, since checking only the
light shell is exactly how both of those reached the live site.

What the deployed page proves, measured rather than eyeballed: **zero colour
collisions across all 16 panels**, records and moneylines rendering from real
data, 1,148 `data-label`s and 352 tooltips, 16/16 panels expanded in print with
the tab strip and logos suppressed, and no horizontal overflow at 375px. The
one colour decision that needed no correction was `trun_ink_for()` -- the
team-coloured captions measured 6.01:1 on the live page. Computed beat assumed.

Two things about WordPress.com worth knowing next time. Its **page cache does
not purge on a plugin deploy**, so the first look at a deployed change can
silently be the old page -- check with a cache-busting query before believing
anything, and note that the CDN caches the plugin's CSS URL the same way. And
it rewrites asset URLs to `?m=<deployed mtime>`, so the stylesheet busts on
deploy by itself; `TRUN_VERSION` is still bumped on every release, because that
is the handle this repo controls and production may not behave the same way.

Still to do: the rehearsal itself, then the production cutover (Phase 5). One
dated hazard sits in that window -- the odds fixture is per-week, so the hourly
staging build fails the moment Week 2 opens without a re-recorded fixture. The
`/health` probe's missing retry, which cost one build at 02:45 UTC on
2026-09-08, was fixed in `90efc81`.
