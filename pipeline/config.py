"""Configuration for the Rundown pipeline.

Everything that a human might reasonably want to change lives here rather than
being scattered through the source modules. Secrets come from the environment
and are never written to a file or included in a payload.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------
# Odds
# --------------------------------------------------------------------------

#: Which sportsbook's numbers the article publishes. Change this one string to
#: switch books. Must be a bookmaker key The Odds API recognises, e.g.
#: "draftkings", "fanduel", "betmgm", "williamhill_us".
ODDS_BOOK: Final[str] = os.environ.get("ODDS_BOOK", "draftkings")

#: Human-readable label shown in the page footer as attribution.
ODDS_BOOK_LABELS: Final[dict[str, str]] = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "williamhill_us": "Caesars",
    "betrivers": "BetRivers",
    "pointsbetus": "PointsBet",
}

ODDS_API_BASE: Final[str] = "https://api.the-odds-api.com/v4"
ODDS_SPORT_KEY: Final[str] = "americanfootball_nfl"

#: Markets available on the bulk endpoint, which returns every game in one
#: call and costs len(markets) credits.
ODDS_BULK_MARKETS: Final[tuple[str, ...]] = ("h2h", "spreads", "totals")

#: team_totals is not a featured market, so it needs the per-event endpoint at
#: one call (and one credit) per game -- sixteen of the nineteen credits a
#: build costs. Measured on the 2026 week 1 slate, DraftKings posted the market
#: for none of them: every response came back 200 with an empty `bookmakers`
#: list, and every team total was derived from the spread and total anyway.
#:
#: So a live build probes the market on one hour a day and derives the rest of
#: the time. The lag matters little -- a derived team total tracks the spread
#: and total as they move, where a posted one from this morning would not --
#: and the probe means a book that starts posting it is noticed within a day
#: rather than never. Set to None to stop probing altogether.
#:
#: Recording and replaying ignore this: see `_should_probe_team_totals`.
ODDS_TEAM_TOTALS_PROBE_HOUR: Final[int | None] = 12

#: Warn loudly when the remaining monthly quota drops below this.
ODDS_QUOTA_FLOOR: Final[int] = 5_000

#: Where recorded Odds API responses live. Staging replays one of these instead
#: of calling the API, so test runs cost nothing and return the same numbers
#: every time.
ODDS_FIXTURE_DIR: Final[Path] = Path(__file__).parent / "fixtures"

#: Fail a replayed run when fewer than this share of games match the fixture.
#: Replay joins events to games by team pair, so a fixture from another week
#: matches nothing and every game silently falls back to nflverse lines -- at
#: which point a broken parser still looks like a passing test.
REPLAY_MIN_MATCH_RATE: Final[float] = 0.5


def odds_fixture_path(season: int, week: int) -> Path:
    return ODDS_FIXTURE_DIR / f"odds-live-{season}-wk{week:02d}.json"

# --------------------------------------------------------------------------
# Season handling
# --------------------------------------------------------------------------

#: Through this week, season-to-date samples are too thin to publish, so the
#: stat modules use the prior season and every table is badged accordingly.
PRIOR_SEASON_THROUGH_WEEK: Final[int] = 1

#: Through this week, current-season numbers are shown but carry a visible
#: "n = X games" badge so readers can weigh them.
SMALL_SAMPLE_THROUGH_WEEK: Final[int] = 4


def stats_season(season: int, week: int) -> int:
    """Which season's completed games a stat module should read.

    Week 1 has no current-season results at all, so every module falls back to
    the prior season and is badged accordingly. Keeping the rule here rather
    than in each module means the cutover moves in one place.
    """
    return season - 1 if week <= PRIOR_SEASON_THROUGH_WEEK else season

# --------------------------------------------------------------------------
# Stat modules
# --------------------------------------------------------------------------

#: How many receivers and backs each team's table lists. The mockup shows five
#: and two; three backs covers a genuine committee, which the mockup's example
#: matchup happened not to have.
RECEIVER_ROWS: Final[int] = 5
RUSHER_ROWS: Final[int] = 3

#: Carries per game a back needs before he belongs in a workload table.
#: Without a floor the third row fills up with fullbacks and special-teamers --
#: over 2025 that was Reggie Gilliam and Kyle Juszczyk at a tenth of a carry a
#: game. They are real players with real snap shares, but "workload" is the
#: column heading, and theirs is not a rushing workload. Expressed per game
#: rather than as a season total so it means the same thing in week 2 as in
#: week 12.
RUSHER_MIN_ATT_PER_GAME: Final[float] = 1.0

#: Pace is measured in neutral game states only, because trailing teams hurry
#: and leading teams stall -- which says more about the scoreboard than about
#: the offense.
PACE_WP_RANGE: Final[tuple[float, float]] = (0.20, 0.80)
PACE_DOWNS: Final[tuple[int, ...]] = (1, 2)

#: Longest snap-to-snap gap still counted as pace. Anything above this is a
#: timeout, an injury, a replay review, or a TV break -- the broadcast rather
#: than the huddle. Real snap-to-snap intervals sit around 30 seconds, so this
#: only ever trims the tail.
PACE_MAX_GAP_SECONDS: Final[float] = 60.0

# --------------------------------------------------------------------------
# WordPress
# --------------------------------------------------------------------------

WP_SITE_URL: Final[str] = os.environ.get("WP_SITE_URL", "").rstrip("/")
WP_REST_NAMESPACE: Final[str] = "trinity-rundown/v1"

# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

HTTP_TIMEOUT: Final[float] = 30.0
HTTP_RETRIES: Final[int] = 3
USER_AGENT: Final[str] = "trinity-rundown/0.1 (+https://github.com/Bacon-23/nfl-rundown)"

ESPN_INJURIES_URL: Final[str] = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
)
OPEN_METEO_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"

#: Open-Meteo only forecasts this far out; past it we show "TBD" rather than
#: inventing a number.
WEATHER_HORIZON_DAYS: Final[int] = 16


def odds_api_key() -> str:
    """Read the Odds API key, failing loudly rather than silently degrading."""
    key = os.environ.get("ODDS_API_KEY", "")
    if not key:
        raise RuntimeError(
            "ODDS_API_KEY is not set. Set it in the environment, or pass "
            "--no-odds-api to build with the nflverse fallback lines."
        )
    return key


def wp_token() -> str:
    token = os.environ.get("TRINITY_RUNDOWN_TOKEN", "")
    if not token:
        raise RuntimeError(
            "TRINITY_RUNDOWN_TOKEN is not set; cannot authenticate to WordPress."
        )
    return token


def book_label(book: str | None = None) -> str:
    book = book or ODDS_BOOK
    return ODDS_BOOK_LABELS.get(book, book.replace("_", " ").title())
