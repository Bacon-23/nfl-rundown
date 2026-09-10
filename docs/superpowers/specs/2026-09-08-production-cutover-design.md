# Phase 5: the production cutover

Status: approved, not yet implemented. Written 2026-09-08, the day before
Week 1 kickoff.

## What is already true

Phase 5 was gated on a full end-to-end rehearsal. That rehearsal ran on
staging on 2026-09-08, against live Week 1 data, through the writer's screen
rather than WP-CLI. It proved the four things that had
never been tested against input that actually differed: editorial survived
three full-week pushes byte-identical, the freeze held while all 16 games'
odds moved underneath it, an override beat the pipeline on a field rebuilt
hourly, and the opener stayed written-once. Phase 5's precondition is met.

Week 1 is not the launch. Production launches Week 2.

A security pass over the whole trust boundary ran on 2026-09-10, before any of
this was executed. It found nothing in the SQL, escaping, nonce or capability
layers, and three gaps worth closing first, all of which the runbook below now
carries: environments had no deployment branch policy (step 2), `push.py` would
send the bearer token to a non-`https://` `WP_SITE_URL` (step 2, and now
refused in code), and the pipeline/editorial split was enforced by the sender's
schema rather than by the receiver. That last one is a plugin change --
`TRUN_Storage::RESERVED_KEYS` -- so the PHP gates below are real checks on this
work rather than the confirmations they would otherwise have been.

## Decisions

| Question | Answer |
|---|---|
| What is staging for after launch? | **On-demand only.** No scheduled builds; manual dispatch when testing a change. |
| When does production's cron start? | **Immediately at cutover**, during Week 1. |
| How does the cron change hands? | **A repo variable**, `SCHEDULED_TARGET`. |
| Failure alerting | **GitHub's built-in email.** No new infrastructure. |

Two of these pay for each other. Retiring the staging cron removes the odds
fixture treadmill entirely: `--replay-odds` resolves its path from season and
week (`build_week.py:429`), so an hourly staging build would have started
failing the moment Week 2 opened, and re-recording costs 19 credits a week now
that a recording always probes team totals. Nothing replays on a schedule any
more, so nothing needs re-recording. The committed
`pipeline/fixtures/odds-live-2026-wk01.json` stays as a historical artifact
with no maintenance attached to it -- every replay test builds its own fixture
in `tmp_path`, so no test depends on it.

Starting production's cron during Week 1 buys a week of live-fire before the
week that matters. Production will hold Week 1 rows nobody publishes, which is
invisible to readers: the pipeline fills rows and never creates posts, so
nothing renders until a writer places the shortcode. It costs roughly 550
credits for the week against a 100K tier -- trivial, but drawn from a
subscription shared with other Trinity products, so worth naming rather than
assuming as headroom.

## The switch

Four edits to `.github/workflows/build-week.yml`. No pipeline changes.

1. **Target resolution** becomes `inputs.environment || vars.SCHEDULED_TARGET
   || 'staging'` in all **four** places that read `inputs.environment ||
   'staging'` today: `concurrency.group`, the job's `environment:` key,
   `TARGET` in the build step's env, and the payload artifact's name. These
   must move together. If `concurrency` disagrees with `environment`, a manual
   staging run and the production cron stop being mutually exclusive and can
   interleave writes into one week's rows.

   *Corrected 2026-09-10, during implementation: this said "three places" and
   omitted the artifact name. That fourth one is cosmetic -- a mislabelled
   download, not a corrupted row -- but a production run uploading
   `payload-staging` is misleading in exactly the window where steps 10 and 11
   have someone inspecting payloads by hand, so it moves with the others.*

2. **The odds mode needs no edit.** The build step already reads
   `if [ "$TARGET" = "production" ]; then MODE=live`. Once `TARGET` resolves
   to `production` on a schedule, live odds follow.

3. **The dispatch `odds` input default** changes from `replay` to `nflverse`,
   so a manual staging test after the week rolls over does not resolve to a
   fixture that was never recorded. `replay` stays selectable for reproducing
   a Week 1 result exactly.

4. **The scheduled fallback** `else MODE=replay` becomes `nflverse`, so that a
   staging cron re-enabled later degrades to free current-week lines rather
   than dying on an absent file.

`CRON_ENABLED` keeps its meaning and stays `true`: it is the master
off-switch, `SCHEDULED_TARGET` is the selector. Two knobs, one job each.

**The cost of this approach, stated plainly.** The live target now lives in a
GitHub settings page, and the workflow file alone no longer answers "where do
scheduled builds go?". That was accepted deliberately, because the rollback
path during a live week has to be a dropdown rather than a commit -- today a
push to `main` also redeploys the plugin to staging, so committing the target
would make "stop the cron" and "ship code" share a trigger. It is paid down by
writing both variables and their live values into the README, and by the
`Target: <env> | odds: <mode>` line the build step already echoes, so any run's
log says what it did even when the file cannot.

## Runbook

Ordered so every irreversible step follows the thing that proves it safe.

### A. Credentials and connection

1. `openssl rand -hex 32`; add `define( 'TRINITY_RUNDOWN_TOKEN', '<new>' );`
   to production's `wp-config.php` over SFTP, above the stop-editing line. A
   **different string from staging's** -- that is what makes a mistyped
   `WP_SITE_URL` fail loudly instead of writing to the wrong database.
2. Create GitHub Environment `production` holding `WP_SITE_URL` (live domain,
   **`https://` scheme**, no trailing slash) and `TRINITY_RUNDOWN_TOKEN`. The
   scheme is not cosmetic: the bearer token travels with the request, so
   `push.endpoint()` refuses a non-`https://` value rather than let it reach
   the wire. Then lock the environment to `main`, because a run naming it can
   otherwise read both secrets from any branch, into public logs:

   ```
   gh api -X PUT repos/Bacon-23/nfl-rundown/environments/production -F "deployment_branch_policy[protected_branches]=false" -F "deployment_branch_policy[custom_branch_policies]=true"
   gh api -X POST repos/Bacon-23/nfl-rundown/environments/production/deployment-branch-policies -f name=main -f type=branch
   ```

   Do the same for `staging`, which has carried no policy since it was created.
   Leave `can_admins_bypass` at its default: it is what keeps manual dispatch
   working.
3. Connect GitHub Deployments on the **production** site: branch `main`,
   destination `/wp-content/plugins/trinity-rundown`, **advanced mode**,
   **automatic off**.
4. Immediately after: check `main` for a WordPress.com-generated commit and
   diff `.github/workflows/wpcom.yml`. Connecting force-writes that file with
   its generated default of `path: [., !.git*]`.

   The known-good file as of 2026-09-10 is pinned by hash, so this is a
   comparison rather than a from-memory reading:

   ```
   git fetch origin main
   git cat-file -p origin/main:.github/workflows/wpcom.yml | sha256sum
   # 2f01bfc7cbc4760ec8cbc09286b0a1ae888588f1ac01d28bda8644e8daca30d8
   ```

   Hash the **committed blob**, not the working-tree file: git stores this
   repository LF-only under `.gitattributes`, so a Windows checkout hashes
   differently for reasons that have nothing to do with WordPress.com.

   A mismatch means restore `path: wordpress/trinity-rundown`,
   `if-no-files-found: error`, and the artifact name `wpcom` -- then re-hash
   until it matches. Either way, verify with `gh run download <id> -n wpcom`
   that `trinity-rundown.php` sits at the **artifact root**; nested, and both
   sites' next deploy ships a plugin WordPress cannot detect. If a deliberate
   change to this file is ever made, update the hash above in the same commit.

### B. Plugin onto production

5. Deploy manually from WordPress.com. Verify the files landed, re-deriving
   production's site root rather than assuming `/srv/htdocs` -- that is a
   staging fact and production inherits nothing.
6. Activate the plugin; confirm `wp_trinity_rundown_games` was created.

### C. Prove the transport before trusting it

7. Anonymous `/health` probe against production. Reachability should be
   uninteresting on a public site, but the token pairing is new and this is
   what tests it.
8. **Cross-environment rejection test.** Staging's token against production's
   URL must return 403, and the reverse must return 403.
9. Confirm production's table is still empty -- that nothing wrote to it
   during any of the above.
9b. Dispatch `build-week` from a non-`main` branch against `environment=
    production` and confirm GitHub refuses it. The branch policy is the only
    thing standing between a pushed branch and the live token, so it is worth
    one deliberate attempt to see it hold.

### D. First real write, hand-driven

10. Dispatch `environment=production, week=1, odds=live, push=false`. Inspect
    the payload artifact. This path skips the health probe by design, which is
    why step 7 stands separately.
11. Dispatch again with `push=true`. Confirm with
    `wp rundown status --season=2026 --week=1`. Cache-bust anything fetched
    over HTTP before believing it.

### E. Hand the cron over

12. Set repo variable `SCHEDULED_TARGET=production`.
13. Watch the next scheduled run: the log must read
    `Target: production | odds: live`, with a sane `x-requests-remaining`.
14. Confirm staging's rows stop moving. The switch has to move the cron, not
    add a second one.

### Recommended, optional

15. Run publish, freeze, push and unlock on **production** against a **draft**
    post during Week 1. Invisible to readers, and it exercises the freeze on
    the live database rather than trusting that staging's rehearsal transfers.

Week 2 additionally needs the writer to create the live post carrying the
`[rundown_week]` shortcode.

## Testing

**New:** `pipeline/tests/test_workflow_targets.py`, written before the
workflow edits and watched to fail first. It parses `build-week.yml` and
asserts that the four target expressions are identical strings, that
`production` maps to `live` in the odds-mode block, and that the dispatch
`odds` default is not `replay`. The first of those is the only failure in this
work that corrupts data rather than erroring.

This needs `pyyaml` added to the `[dev]` extra. It is currently present in the
local venv with an empty `Required-by:` and is declared in neither
`dependencies` nor `[dev]`, so a YAML-parsing test would pass here and fail in
CI, where the install is `pip install -e ".[dev]"`.

**Unchanged and expected green:** the existing 195 offline tests and 8 live
(the new file adds to the offline count), `php -l`, phpcs on the committed
ruleset, and the CRLF byte check. No plugin files change here, so the PHP
gates are confirmations rather than checks.

**Ops verification** is the 14 steps, each producing evidence rather than an
impression: row counts from `wp rundown status`, both 403 bodies from the
isolation test, the target log line, staging's frozen `updated_at`, quota
remaining.

## Rollback

| Goes wrong | Recovery | Cost |
|---|---|---|
| Cron writing bad data | `SCHEDULED_TARGET` to `staging`, or unset `CRON_ENABLED` to stop everything | Next hour, no push |
| Bad plugin on production | Re-deploy an earlier commit. Deploys merge rather than replace, so files that must disappear need the directory deleted on the server first | Manual, minutes |
| Wrong stats in a row | Re-run; the build is idempotent and `stats_json` is pipeline-owned | Automatic |
| Editorial clobbered | Cannot happen by design -- separate columns, proven over three pushes in the rehearsal | -- |
| Bad `opening_line` | **No clean path.** Write-once at the DB layer; a wrong opener needs a direct row edit | Manual DB surgery |

The last row is the one to keep in view. It is the only state in this system a
re-run cannot repair, and the cutover schedules production's first-ever Week 2
build to be the one that sets it. Starting the cron during Week 1 is what
de-risks it: by that Tuesday the hourly path will have run about 150 times.

## Accepted gaps

- **Silent degradation between builds is not alerted.** A build that succeeds
  while publishing nflverse fallback lines instead of the book's produces no
  notification. Partly mitigated already: the payload carries
  `odds_source: "nflverse_fallback"` and the admin screen banners it, so the
  writer sees it before publishing. Only unattended hours are uncovered.
  Revisit after Week 2.
- **Production deploys stay manual**, so a future plugin change needs a
  deliberate "Deploy now". That is the plan's decision and holds at least
  through Week 2.

## Out of scope

DvP, anytime-TD props, "last 4 weeks" trend columns, the paid routes-run
adapter, and the line-movement sparkline -- all Phase 5 extensions that follow
the cutover rather than belonging to it.
