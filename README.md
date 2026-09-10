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
| `WP_SITE_URL` | `--push` | e.g. `https://example.com`. **Must start with `https://`**, no trailing slash — the push refuses anything else rather than put the token on the wire in cleartext. |
| `TRINITY_RUNDOWN_TOKEN` | `--push` | Must match the constant in that site's `wp-config.php`. |
| `ODDS_BOOK` | optional | Defaults to `draftkings`. |

## Two environments

Staging and production are separate WordPress sites with separate databases and
**separate tokens**. One token per site is deliberate: a mistyped `WP_SITE_URL`
then fails loudly instead of quietly writing to the wrong database.

That covers the wrong *site*. The other half is the wrong *scheme*: the bearer
token is sent with the request, so `endpoint()` in `pipeline/push.py` refuses a
`WP_SITE_URL` that is not `https://` before building the request at all. The
plugin's own `is_ssl()` check cannot help there — by the time it runs, the
credential has already crossed the wire.

| | Staging | Production |
|---|---|---|
| Plugin deploy | GitHub Deployments, automatic on push to `main` | GitHub Deployments, manual |
| Scheduled builds | on-demand dispatch only after launch | the cron target |
| Odds | free nflverse lines, or a fixture on request | live Odds API |
| Secrets | GitHub Environment `staging` | GitHub Environment `production` |

Two repo variables steer the cron, one job each (Settings → Secrets and
variables → Actions → Variables):

| Variable | Live value | What it does |
|---|---|---|
| `CRON_ENABLED` | `true` | The master off-switch. Unset it and every scheduled build stops. Manual `workflow_dispatch` ignores it, so setup stays testable. |
| `SCHEDULED_TARGET` | *unset until cutover step 12; then* `production` | Which site the cron writes to. Unset, it falls back to `staging`. |

**The workflow file alone no longer answers "where do scheduled builds go?"**
That was accepted deliberately: a push to `main` also redeploys the plugin to
staging, so keeping the target in the file would make "stop the cron" and
"ship code" share a trigger — and the rollback during a live week has to be a
dropdown, not a commit. The price is this table, and the `Target: <env> |
odds: <mode>` line every build echoes, so a run's log says what it did even
when the file cannot.

`SCHEDULED_TARGET` is read in four places — `concurrency.group`, the job's
`environment:`, `TARGET`, and the payload artifact's name. They must stay
byte-identical: a `concurrency` group that disagrees with `environment` lets a
manual run and the cron interleave writes into one week's rows, which corrupts
data rather than erroring. `pipeline/tests/test_workflow_targets.py` pins all
four.

`WP_SITE_URL` and `TRINITY_RUNDOWN_TOKEN` live in **GitHub Environments**, not
repo-level secrets, so a job only ever holds the credential for the site it
declares. `ODDS_API_KEY` is repo-level, since one subscription serves both.

Each environment **must** carry a **deployment branch policy limiting it to
`main`**. Without one, any workflow run naming the environment can read its
secrets from any branch — including a branch carrying an edited
`build-week.yml`, whose logs are public because this repository is. The policy
also stops an accidental dispatch from a work-in-progress branch writing to a
live site.

**Neither environment carries one yet.** `staging` has had none since it was
created, and `production` does not exist until the cutover. Both are applied in
the cutover runbook (step 2 of
[`docs/superpowers/specs/2026-09-08-production-cutover-design.md`](docs/superpowers/specs/2026-09-08-production-cutover-design.md));
until then, treat the paragraph above as the requirement rather than the state.
Leave `can_admins_bypass` at its default — that is what keeps manual dispatch
working.

> **Do not use WordPress.com's "Push to Production" sync.** Its dialog offers to
> copy the database, which would overwrite live posts with staging content.
> Plugin code reaches each site from git, independently.

### Recording the odds fixture

Dispatching **Build week** with `odds: replay` replays a captured API response,
so a test run costs nothing and returns the same numbers every time. It is
opt-in rather than any environment's default: the fixture path resolves from
season and week, so a default of `replay` would start failing the hour a new
week opened. The committed `pipeline/fixtures/odds-live-2026-wk01.json` is a
historical artifact with no maintenance attached — every replay test builds its
own fixture in `tmp_path`, so nothing in the suite depends on it.

Record a new one only to reproduce a specific week by hand, and do it from CI
rather than a workstation so the API key stays in Actions secrets: dispatch
**Build week** with `odds: record` and `push: false`, then download the
`odds-fixture` artifact and commit it to `pipeline/fixtures/`.

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

**The end-to-end rehearsal ran on staging on 2026-09-08**, against live Week 1
data, through the writer's screen rather than WP-CLI. Commentary and a weather
override went into two games, the week was published, the pipeline was run
against it, and the week was unlocked again.

What it proves, measured on the public page rather than eyeballed:

- **Editorial survives the pipeline.** Three pushes, each rewriting all 16 rows
  (`0 inserted, 16 updated`), left the six note bodies byte-identical.
- **The freeze holds against real movement.** Diffing the replayed payload
  against a live one, **all 16 games' odds had moved** -- six spreads, eight
  totals -- while the published page changed **0 of its 160 odds cells**. This
  is the invariant the freeze exists for, and it had never been tested against
  input that actually differed.
- **Overrides beat the pipeline even on a field it rewrites hourly.** The
  weather override held through a save, a publish, two pushes and an unlock.
- **The opener is written once.** Every push reported `0 openers` against a
  week whose openers were captured on 2026-09-02.
- **Drift is surfaced, and unlock releases.** The admin showed "the pipeline
  has moved since this week was published"; unlocking moved 32 of 160 cells to
  live values, six summaries gaining their line movement -- `SEA -3 (opened
  SEA -3.5)`.

Two things only staging could show. The admin's Publish and Unlock buttons use
`window.confirm()`, which blocks browser automation outright -- override it in
the page before clicking either. And **weather is fetched live on every build
regardless of the odds mode**, so two replay runs are never byte-identical;
any "did the page change?" check has to account for that or it will look
alarming for no reason.

Phase 5's precondition -- a full rehearsal on staging before production is
touched -- is therefore met.

**Home and away splits landed 2026-09-09**, adding two blocks the page had
never carried: PPR at home and on the road for each team's quarterback plus its
four highest-scoring skill players, and kicker accuracy by venue.

Most of this was free. `nflreadpy.load_player_stats()` already ships
`fantasy_points_ppr` per player per week -- standard PPR with **four-point
passing touchdowns**, verified by hand against Aaron Rodgers' week 4 -- along
with the full kicking box score, and `game_id` joins a player-week to the
schedule that knows which side was at home. Nothing needed computing.

What was not free was the kicker. **nflverse scores every kicker 0.0**: its
fantasy formula excludes kicking outright, and the only non-zero kicker-week in
2025 is Brandon Aubrey's six rushing yards on a fake, in a game where he also
made four field goals worth nothing. So the kicking table reports made,
attempted, long, and volume, and carries no points column at all -- any number
there would be a scoring rule we invented, and no two leagues agree on one. A
live test holds that claim so a change upstream reopens the decision rather
than silently leaving a column off.

*The window is the one place these tables diverge from the rest of the page.*
They read a trailing 17 games per player rather than a season, because a venue
split halves whatever sample it is handed: under the season rule, weeks 2 to 7
hold one to three games per venue and both tables would be six weeks of dashes.
That costs a fourth `basis` value and one badge string, `last 17 games`. Since
the badge then describes the window rather than any one player, each venue cell
carries its own count -- `20.0 (8)` -- and a side below three games prints
`-- (2)`, which is deliberately a different statement from a bare dash.

Two things the build itself found:

- **Three teams have no kicking table on opening weekend**, and that is
  correct. Green Bay, the Giants and Washington all start rookie kickers with
  no NFL history to split. The missing-team warning rests on "every team has
  one", which holds for receivers and backs but not for a kicker measured over
  a trailing window, so kickers are excluded from it -- a warning that fires
  every build is one nobody reads.
- **`away` and `home` were already taken.** At module level those keys mean
  *which team*, and `trun_module_sides()` is built around that. The venue axis
  therefore lives inside each row -- `ppr_home`, `ppr_away`, and a `venue`
  field on the kicker rows -- rather than as a second level that would have
  overloaded the helper.

Gates: 225 offline tests and 11 live, `php -l` on all eight PHP files, phpcs
clean on the committed ruleset, and no CRLF. The rendered page was checked at
900px and 375px, light and dark, and against a payload with both new keys
stripped -- the two sections vanish and everything else renders unchanged. The
phone reflow was the risk worth checking: `rundown.css` enumerates its stacking
selectors per table class rather than matching generically, so a new class that
misses one of the five lists looks right on a desktop and becomes an unlabelled
column of bare numbers on a phone. Both new classes are in all five, verified
in a browser rather than by reading the file.

Still to do: the production cutover (Phase 5). One dated hazard sits in that
window -- the odds fixture is per-week, so the hourly staging build fails the
moment Week 2 opens without a re-recorded fixture, and recording now costs the
full 19 credits rather than 3, since a recording always probes team totals. The
`/health` probe's missing retry, which cost one build at 02:45 UTC on
2026-09-08, was fixed in `90efc81`.
