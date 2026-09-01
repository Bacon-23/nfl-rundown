"""Assemble one week's payload and, optionally, push it to WordPress.

    python -m pipeline.build_week --season 2026 --week 1 --dry-run
    python -m pipeline.build_week --season 2026 --week 1 --push

Phase 0/1 builds the header, odds bar, and venue. The stat modules attach in
Phase 2 through `_attach_modules`, which is the only function that needs to
change as they land.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from pipeline import config
from pipeline.metrics import records as records_metric
from pipeline.metrics.records import TeamRecords
from pipeline.schema import Game, Kickoff, Team, WeekPayload
from pipeline.sources import injuries as injuries_source
from pipeline.sources import odds as odds_source
from pipeline.sources import schedule as schedule_source
from pipeline.sources import weather as weather_source
from pipeline.sources.odds_tape import StaleFixtureError
from pipeline.sources.schedule import ScheduledGame, format_kickoff
from pipeline.sources.team_map import team_meta

log = logging.getLogger("rundown.build")


def build(
    season: int,
    week: int,
    *,
    use_odds_api: bool = True,
    record_odds: Path | None = None,
    replay_odds: Path | None = None,
) -> tuple[WeekPayload, list[str]]:
    """Build a week. Returns the payload and any warnings worth surfacing."""
    games = schedule_source.load_week(season, week)
    log.info("Loaded %d scheduled games for %s week %s.", len(games), season, week)

    odds_result = odds_source.fetch(
        games,
        use_api=use_odds_api,
        record=record_odds,
        replay=replay_odds,
    )
    log.info("Odds source: %s", odds_result.source)

    meta = team_meta()

    built: list[Game] = []
    for index, game in enumerate(games):
        built.append(
            Game(
                game_id=game.game_id,
                season=season,
                week=week,
                sort_order=index,
                away=_team(game.away, meta, moneyline=game.away_moneyline),
                home=_team(game.home, meta, moneyline=game.home_moneyline),
                kickoff=Kickoff(
                    utc=game.kickoff_utc,
                    display=format_kickoff(game.kickoff_utc),
                ),
                odds=odds_result.odds[game.game_id],
            )
        )

    module_warnings = _attach_modules(built, games, season, week)

    payload = WeekPayload(
        season=season,
        week=week,
        generated_at=datetime.now(UTC),
        games=built,
    )

    return payload, [*odds_result.warnings, *module_warnings]


def _team(abbr: str, meta: dict, *, moneyline: int | None) -> Team:
    info = meta.get(abbr, {})
    return Team(
        abbr=abbr,
        name=info.get("name") or abbr,
        color=info.get("color"),
        logo=info.get("logo"),
        moneyline=moneyline,
    )


def _attach_modules(
    built: list[Game],
    scheduled: list[ScheduledGame],
    season: int,
    week: int,
) -> list[str]:
    """Hang the per-game modules off each game, returning any warnings.

    Each module is independent and self-contained: a failure in one leaves the
    rest of the page intact. Efficiency, passing, and rushing attach here in
    Phase 2 without touching the surrounding build logic.
    """
    warnings: list[str] = []
    by_id = {game.game_id: game for game in built}

    # --- Weather -----------------------------------------------------------
    forecasts, weather_warnings = weather_source.fetch(scheduled)
    warnings.extend(weather_warnings)
    for game_id, forecast in forecasts.items():
        by_id[game_id].weather = forecast

    # --- ATS and over/under records ----------------------------------------
    # Week 1 reads last season, which is the only place completed games exist.
    records_season = config.stats_season(season, week)
    try:
        _attach_records(built, records_metric.load_records(records_season))
    except Exception as exc:  # noqa: BLE001 - one module must not sink the page
        warnings.append(f"Season records unavailable ({records_season}): {exc}")

    # --- Injuries ----------------------------------------------------------
    teams = {g.away for g in scheduled} | {g.home for g in scheduled}

    try:
        rows_by_team = injuries_source.fetch(teams)
    except injuries_source.InjuriesUnavailable as exc:
        # Leave `injuries` as None rather than an empty list. WordPress keeps
        # whatever it already has, so a transient ESPN failure never blanks a
        # good report off the page.
        warnings.append(f"Injuries unavailable, keeping the stored table: {exc}")
        return warnings

    matched = injuries_source.enrich_with_practice(rows_by_team, season, week)
    if matched:
        log.info("Practice participation added to %d injury rows.", matched)

    for game in scheduled:
        by_id[game.game_id].injuries = (
            rows_by_team.get(game.away, []) + rows_by_team.get(game.home, [])
        )

    return warnings


def _attach_records(built: list[Game], records: dict[str, TeamRecords]) -> None:
    """Write each side's ATS and over/under record onto the built game.

    A team the tally never saw, or one whose record is empty, is left as None
    rather than "0-0" -- the renderer shows a dash, which is honest, where
    "0-0" reads as a real record of no games won.
    """
    for game in built:
        for team in (game.away, game.home):
            record = records.get(team.abbr)
            if record is None:
                continue
            team.ats_record = str(record.ats) or None
            team.ou_record = str(record.ou) or None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_week",
        description="Build a Rundown week payload from public NFL data.",
    )
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument(
        "--week",
        required=True,
        help="Week number, or 'auto' to use the week holding the next kickoff.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Write the payload to this path (defaults to build/<season>-week-<NN>.json).",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="POST the payload to WordPress. Requires WP_SITE_URL and TRINITY_RUNDOWN_TOKEN.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the payload to disk and print a summary without pushing.",
    )
    parser.add_argument(
        "--no-odds-api",
        action="store_true",
        help="Skip The Odds API and use nflverse lines. Costs no credits.",
    )
    parser.add_argument(
        "--record-odds",
        nargs="?",
        const="auto",
        metavar="PATH",
        help="Fetch live and save the raw responses as a replay fixture. "
        "Omit PATH to use the default fixture location for this week.",
    )
    parser.add_argument(
        "--replay-odds",
        nargs="?",
        const="auto",
        metavar="PATH",
        help="Serve odds from a recorded fixture instead of the API. "
        "Costs no credits and returns identical results every run.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.push and args.dry_run:
        parser.error("--push and --dry-run are mutually exclusive.")

    if str(args.week).lower() == "auto":
        week = schedule_source.current_week(args.season)
        log.info("Auto-detected week %d.", week)
    else:
        try:
            week = int(args.week)
        except ValueError:
            parser.error(f"--week must be a number or 'auto', got {args.week!r}.")

    if args.record_odds and args.replay_odds:
        parser.error("--record-odds and --replay-odds are mutually exclusive.")

    record = _fixture_path(args.record_odds, args.season, week)
    replay = _fixture_path(args.replay_odds, args.season, week)

    if record:
        log.info("Recording odds responses to %s.", record)
    if replay:
        log.info("Replaying odds from %s.", replay)

    try:
        payload, warnings = build(
            args.season,
            week,
            use_odds_api=not args.no_odds_api,
            record_odds=record,
            replay_odds=replay,
        )
    except StaleFixtureError as exc:
        # Operator error, not a crash. A traceback would bury the one line
        # that says what to do about it.
        print(f"\nOdds fixture problem: {exc}", file=sys.stderr)
        return 2

    out = args.out or Path("build") / f"{args.season}-week-{week:02d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload.wire(), indent=2), encoding="utf-8")

    _summarise(payload, warnings, out)

    if args.push:
        from pipeline.push import push_week

        result = push_week(payload)
        print(
            f"\nPushed: {result.get('inserted', 0)} inserted, "
            f"{result.get('updated', 0)} updated, "
            f"{result.get('openers_set', 0)} openers recorded.",
            file=sys.stderr,
        )

    # Warnings are informational. A run that fell back to nflverse lines still
    # produced a usable page, so it should not fail the workflow.
    return 0


def _fixture_path(value: str | None, season: int, week: int) -> Path | None:
    """Resolve a fixture flag: absent, bare (default location), or explicit."""
    if not value:
        return None
    if value == "auto":
        return config.odds_fixture_path(season, week)
    return Path(value)


def _summarise(payload: WeekPayload, warnings: list[str], out: Path) -> None:
    lines = [
        f"{payload.season} week {payload.week}: {len(payload.games)} games -> {out}",
        "",
    ]

    for game in payload.games:
        spread = "PK" if game.odds.spread == 0 else (
            f"{game.odds.spread_favorite} {game.odds.spread:g}"
            if game.odds.spread is not None
            else "--"
        )
        totals = (
            f"{game.away.abbr} {game.odds.away_team_total}"
            f" / {game.home.abbr} {game.odds.home_team_total}"
            if game.odds.away_team_total is not None
            else "--"
        )
        derived = " (derived)" if game.odds.team_totals_derived else ""
        lines.append(
            f"  {game.away.abbr:>3} @ {game.home.abbr:<3}  "
            f"{game.kickoff.display:<16} {spread:>9}  "
            f"o/u {game.odds.total or '--':<5} tt {totals}{derived}"
        )

        # "--" distinguishes "not determined" from an injury count of zero.
        injuries = "--" if game.injuries is None else str(len(game.injuries))
        weather = game.weather.summary if game.weather else "--"
        lines.append(f"        injuries {injuries:>3}  |  {weather}")

    if warnings:
        lines += ["", "Warnings:"]
        lines += [f"  - {w}" for w in warnings]

    print("\n".join(lines), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
