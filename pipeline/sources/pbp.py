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
from dataclasses import dataclass, field
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


#: The columns behind the scoring-area counts. Kept out of `REQUIRED_COLUMNS`
#: on purpose: if one of these moves upstream, the red zone columns go dark and
#: team efficiency does not go with them. `scoring_usage` checks them itself.
SCORING_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "season_type",
        "posteam",
        "play_type",
        "qb_kneel",
        "yardline_100",
        "air_yards",
        "receiver_player_id",
        "rusher_player_id",
        "sack",
        "qb_scramble",
        "two_point_attempt",
    }
)

#: The play-level half of defense vs. position: red zone looks and the
#: longest gain in a game, neither of which the weekly box score carries.
#: Checked by `metrics/dvp.py`, apart from everything else, for the same
#: reason as `SCORING_COLUMNS`.
DVP_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "season_type",
        "game_id",
        "play_type",
        "qb_kneel",
        "yardline_100",
        "yards_gained",
        "complete_pass",
        "receiver_player_id",
        "rusher_player_id",
        "sack",
        "qb_scramble",
        "two_point_attempt",
    }
)

#: Opponent's 20 or closer.
RED_ZONE_YARDS: Final[int] = 20

#: Opponent's 5 or closer.
GOAL_LINE_YARDS: Final[int] = 5


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


@dataclass(frozen=True)
class ScoringUsage:
    """Scoring-area counts, per player and per offense.

    Each count is a triple: red zone targets, end zone targets, carries inside
    the 5. The team triples are the denominators for the shares.
    """

    players: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    teams: dict[str, tuple[int, int, int]] = field(default_factory=dict)


def scoring_usage(frame: pl.DataFrame) -> ScoringUsage:
    """Red zone targets, end zone targets and carries inside the 5.

    A target is a pass play with a named receiver and no sack; two-point tries
    are left out because the weekly player stats leave them out of targets,
    and the two counts should agree. Penalty-erased plays are `no_play` and
    drop out on `play_type`.

    nflverse has no end-zone flag. A pass whose air yards reach the goal line
    is the standard proxy, and a pass with no recorded air yards is not
    counted as one.

    A carry inside the 5 is a designed run: scrambles and kneels are out, and
    a quarterback sneak is in -- it is a goal-line carry the backs did not get.
    """
    check_columns(frame, SCORING_COLUMNS, "Play-by-play (scoring area)")

    plays = frame.filter(
        (pl.col("season_type") == REGULAR_SEASON)
        & pl.col("posteam").is_not_null()
        & (pl.col("two_point_attempt").fill_null(0) == 0)
    )

    in_red_zone = (pl.col("yardline_100") <= RED_ZONE_YARDS).fill_null(False)
    # Reaches the goal line. A deep shot from midfield counts: an end zone
    # target from outside the 20 is still one, just not a red zone target.
    into_end_zone = (pl.col("air_yards") >= pl.col("yardline_100")).fill_null(False)

    targets = plays.filter(
        (pl.col("play_type") == "pass")
        & pl.col("receiver_player_id").is_not_null()
        & (pl.col("sack").fill_null(0) == 0)
        & (in_red_zone | into_end_zone)
    ).select(
        pl.col("posteam"),
        pl.col("receiver_player_id").alias("player_id"),
        in_red_zone.cast(pl.Int64).alias("rz"),
        into_end_zone.cast(pl.Int64).alias("ez"),
        pl.lit(0, dtype=pl.Int64).alias("i5"),
    )

    carries = plays.filter(
        (pl.col("play_type") == "run")
        & pl.col("rusher_player_id").is_not_null()
        & (pl.col("qb_scramble").fill_null(0) == 0)
        & (pl.col("qb_kneel").fill_null(0) == 0)
        & (pl.col("yardline_100") <= GOAL_LINE_YARDS)
    ).select(
        pl.col("posteam"),
        pl.col("rusher_player_id").alias("player_id"),
        pl.lit(0, dtype=pl.Int64).alias("rz"),
        pl.lit(0, dtype=pl.Int64).alias("ez"),
        pl.lit(1, dtype=pl.Int64).alias("i5"),
    )

    counted = pl.concat([targets, carries])
    sums = [pl.col("rz").sum(), pl.col("ez").sum(), pl.col("i5").sum()]

    return ScoringUsage(
        players={
            row["player_id"]: (int(row["rz"]), int(row["ez"]), int(row["i5"]))
            for row in counted.group_by("player_id").agg(sums).iter_rows(named=True)
        },
        teams={
            row["posteam"]: (int(row["rz"]), int(row["ez"]), int(row["i5"]))
            for row in counted.group_by("posteam").agg(sums).iter_rows(named=True)
        },
    )


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
    "DVP_COLUMNS",
    "GOAL_LINE_YARDS",
    "RED_ZONE_YARDS",
    "REQUIRED_COLUMNS",
    "SCORING_COLUMNS",
    "PlayByPlayUnavailable",
    "ScoringUsage",
    "check_columns",
    "clear_cache",
    "load",
    "offensive_plays",
    "scoring_usage",
    "team_dropbacks",
]
