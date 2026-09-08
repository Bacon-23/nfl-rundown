"""Play-by-play from nflverse: the source behind every team efficiency number.

Two jobs beyond fetching. First, the frame is memoised per process -- a season
of play-by-play is roughly 40 MB over the wire and nflreadpy's default cache
lives in memory only, so without this every module that asks pays the download
again.

Second, the column guard. nflverse's parquet schemas move upstream without
announcement, and a renamed column would otherwise surface as a page full of
dashes on a Sunday morning. `REQUIRED_COLUMNS` fails the module loudly instead,
naming what went missing. A committed fixture cannot do this job, because a
fixture never drifts.
"""

from __future__ import annotations

import logging
from typing import Final

import nflreadpy as nfl
import polars as pl

log = logging.getLogger(__name__)

#: Weeks 1-18. nflverse marks the postseason 'POST', which never counts toward
#: a number badged "2025 season".
REGULAR_SEASON: Final[str] = "REG"

#: Every column this package reads out of play-by-play. Checked on load.
REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "season_type",
        "game_id",
        "play_id",
        "posteam",
        "play_type",
        "qb_kneel",
        "qb_spike",
        "qb_dropback",
        "pass_oe",
        "epa",
        "down",
        "wp",
        "fixed_drive",
        "game_seconds_remaining",
    }
)


class PlayByPlayUnavailable(RuntimeError):
    """Play-by-play could not be read, or no longer has the shape we parse."""


_cache: dict[int, pl.DataFrame] = {}


def load(season: int) -> pl.DataFrame:
    """One season of play-by-play, downloaded at most once per process."""
    if season not in _cache:
        try:
            frame = nfl.load_pbp(season)
        except Exception as exc:  # noqa: BLE001 - network, upstream, or parse
            raise PlayByPlayUnavailable(
                f"Could not load {season} play-by-play: {exc}"
            ) from exc

        frame = frame.collect() if isinstance(frame, pl.LazyFrame) else frame
        check_columns(frame, REQUIRED_COLUMNS, f"{season} play-by-play")

        log.info("Loaded %d play-by-play rows for %s.", frame.height, season)
        _cache[season] = frame

    return _cache[season]


def offensive_plays(frame: pl.DataFrame) -> pl.DataFrame:
    """Regular-season pass and run plays, kneels and spikes removed.

    Every efficiency metric starts here rather than filtering for itself, so
    "play" means one thing across the module. Kneels and spikes are clock
    management, not play-calling: counting them makes a team that protected a
    lead look more run-heavy and slower than it played.
    """
    return frame.filter(
        (pl.col("season_type") == REGULAR_SEASON)
        & pl.col("posteam").is_not_null()
        & pl.col("play_type").is_in(["pass", "run"])
        & (pl.col("qb_kneel").fill_null(0) == 0)
        & (pl.col("qb_spike").fill_null(0) == 0)
    )


def team_dropbacks(frame: pl.DataFrame) -> dict[str, int]:
    """Regular-season dropbacks per team, the denominator behind TGT RATE.

    Dropbacks rather than pass attempts: a sack or a scramble was still a
    passing play, and a receiver ran a route on it.
    """
    counted = frame.filter(
        (pl.col("season_type") == REGULAR_SEASON)
        & pl.col("posteam").is_not_null()
        & (pl.col("qb_dropback").fill_null(0) == 1)
    )
    tally = counted.group_by("posteam").agg(dropbacks=pl.len())
    return {row["posteam"]: int(row["dropbacks"]) for row in tally.iter_rows(named=True)}


def check_columns(frame: pl.DataFrame, required: frozenset[str], what: str) -> None:
    """Fail with the names, not with a KeyError three functions deeper."""
    missing = sorted(required - set(frame.columns))
    if missing:
        raise PlayByPlayUnavailable(
            f"{what} is missing {len(missing)} expected column(s): "
            f"{', '.join(missing)}. nflverse's schema has moved; check the "
            f"pinned nflreadpy version in pyproject.toml."
        )


def clear_cache() -> None:
    """Drop the memoised frames. Tests use this; a build never needs it."""
    _cache.clear()


__all__ = [
    "REGULAR_SEASON",
    "REQUIRED_COLUMNS",
    "PlayByPlayUnavailable",
    "check_columns",
    "clear_cache",
    "load",
    "offensive_plays",
    "team_dropbacks",
]
