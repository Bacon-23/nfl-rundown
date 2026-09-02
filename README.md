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

```bash
# Once, against the live API (~3 credits). Commit the result.
python -m pipeline.build_week --season 2026 --week 1 --record-odds --dry-run

# Thereafter
python -m pipeline.build_week --season 2026 --week 1 --replay-odds --dry-run
```

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

Still to build: the writer's admin screen (Phase 3) -- the largest gap, since
without it no commentary can be entered at all -- the stat modules (Phase 2),
and the visual pass against the mockup (Phase 4). The odds fixture in
`pipeline/fixtures/` has not been recorded yet, so the staging cron cannot run
until it is.
