"""Schedule, venue, and fallback lines from nflverse.

This is the spine of a week's payload: it decides which games exist, in what
order, and supplies kickoff, venue, and the odds we fall back to when The Odds
API is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import nflreadpy as nfl
import polars as pl

# US Eastern, which is how the NFL schedules and how readers think about
# kickoff times. Fixed offsets rather than a tz database dependency: the
# regular season runs entirely inside daylight time until early November, so
# the transition is handled explicitly below.
_EASTERN_STANDARD = timezone(timedelta(hours=-5))
_EASTERN_DAYLIGHT = timezone(timedelta(hours=-4))


@dataclass(frozen=True)
class ScheduledGame:
    """One row of the schedule, normalised."""

    game_id: str
    season: int
    week: int
    away: str
    home: str
    kickoff_utc: datetime
    roof: str | None
    surface: str | None
    stadium: str | None
    stadium_id: str | None
    div_game: bool

    # Fallback market data, straight from nflverse.
    spread_line: float | None
    total_line: float | None
    away_moneyline: int | None
    home_moneyline: int | None

    # Result, present only once the game has been played.
    away_score: int | None
    home_score: int | None

    @property
    def is_indoors(self) -> bool:
        return (self.roof or "").lower() in {"dome", "closed"}

    @property
    def is_final(self) -> bool:
        return self.away_score is not None and self.home_score is not None


def load_week(season: int, week: int) -> list[ScheduledGame]:
    """Every game in one week, in kickoff order."""
    frame = _schedules().filter(
        (pl.col("season") == season) & (pl.col("week") == week)
    )

    if frame.height == 0:
        raise ValueError(f"nflverse has no games for {season} week {week}.")

    games = [_to_game(row) for row in frame.iter_rows(named=True)]
    games.sort(key=lambda g: (g.kickoff_utc, g.game_id))
    return games


def load_season(season: int) -> list[ScheduledGame]:
    """Every game in a season, used for ATS and over/under records."""
    frame = _schedules().filter(pl.col("season") == season)
    return [_to_game(row) for row in frame.iter_rows(named=True)]


def current_week(season: int, now: datetime | None = None) -> int:
    """The week the newsroom is working on: the one holding the next kickoff.

    A week stays current until its last game has finished, so a Monday-night
    game does not flip the build to next week while it is still being played.
    Past the end of the season, the final week stays current.
    """
    now = now or datetime.now(UTC)
    games = load_season(season)

    if not games:
        raise ValueError(f"nflverse has no games for {season}.")

    # Roughly the length of a game, so one still in progress counts as current.
    in_progress = timedelta(hours=4)

    upcoming = [g for g in games if g.kickoff_utc + in_progress > now]
    if not upcoming:
        return max(g.week for g in games)

    return min(upcoming, key=lambda g: g.kickoff_utc).week


def _schedules() -> pl.DataFrame:
    frame = nfl.load_schedules()
    return frame.collect() if isinstance(frame, pl.LazyFrame) else frame


def _to_game(row: dict) -> ScheduledGame:
    return ScheduledGame(
        game_id=row["game_id"],
        season=int(row["season"]),
        week=int(row["week"]),
        away=row["away_team"],
        home=row["home_team"],
        kickoff_utc=_kickoff_utc(row.get("gameday"), row.get("gametime")),
        roof=row.get("roof"),
        surface=row.get("surface"),
        stadium=row.get("stadium"),
        stadium_id=row.get("stadium_id"),
        div_game=bool(row.get("div_game") or 0),
        spread_line=_as_float(row.get("spread_line")),
        total_line=_as_float(row.get("total_line")),
        away_moneyline=_as_int(row.get("away_moneyline")),
        home_moneyline=_as_int(row.get("home_moneyline")),
        away_score=_as_int(row.get("away_score")),
        home_score=_as_int(row.get("home_score")),
    )


def _kickoff_utc(gameday, gametime) -> datetime:
    """Combine nflverse's local date and Eastern clock time into UTC.

    `gametime` is Eastern wall-clock. It is occasionally missing for games the
    league has not slotted yet, in which case we assume 1:00pm ET so the game
    still sorts into the right day rather than jumping to midnight UTC.
    """
    date_part = str(gameday)[:10]
    year, month, day = (int(p) for p in date_part.split("-"))

    time_str = str(gametime or "13:00")
    hour, minute = (int(p) for p in time_str.split(":")[:2])

    eastern = _EASTERN_DAYLIGHT if _in_daylight_time(year, month, day) else _EASTERN_STANDARD
    local = datetime(year, month, day, hour, minute, tzinfo=eastern)
    return local.astimezone(UTC)


def _in_daylight_time(year: int, month: int, day: int) -> bool:
    """US DST: second Sunday in March through first Sunday in November."""
    if month in range(4, 11):
        return True
    if month in (1, 2, 12):
        return False

    if month == 3:
        return day >= _nth_weekday(year, 3, weekday=6, n=2)
    return day < _nth_weekday(year, 11, weekday=6, n=1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> int:
    """Day-of-month for the nth given weekday (Monday=0 ... Sunday=6)."""
    first_weekday = datetime(year, month, 1).weekday()
    offset = (weekday - first_weekday) % 7
    return 1 + offset + (n - 1) * 7


def format_kickoff(kickoff_utc: datetime) -> str:
    """Render kickoff the way the mockup does: 'Sun - 5:30pm ET'."""
    eastern = _EASTERN_DAYLIGHT if _in_daylight_time(
        kickoff_utc.year, kickoff_utc.month, kickoff_utc.day
    ) else _EASTERN_STANDARD

    local = kickoff_utc.astimezone(eastern)
    hour = local.hour % 12 or 12
    meridiem = "am" if local.hour < 12 else "pm"
    minute = f":{local.minute:02d}" if local.minute else ""

    return f"{local:%a} · {hour}{minute}{meridiem} ET"


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result  # filter NaN


def _as_int(value) -> int | None:
    result = _as_float(value)
    return None if result is None else int(result)
