"""Weekly player box scores from nflverse.

Targets, receiving yards, carries, and rushing yards, one row per player per
game. Everything in the passing and rushing tables is summed from here; the
snap share that turns targets into a rate comes from `snaps.py`.

Regular season only, for the same reason season records are: a table badged
"2025 season" means the regular season, which is what every other site
publishes.
"""

from __future__ import annotations

import logging
from typing import Final

import nflreadpy as nfl
import polars as pl

from pipeline.sources.pbp import REGULAR_SEASON, PlayByPlayUnavailable, check_columns

log = logging.getLogger(__name__)

REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "player_id",
        "player_display_name",
        "position",
        "season",
        "week",
        "season_type",
        "team",
        "targets",
        "receiving_yards",
        "carries",
        "rushing_yards",
    }
)


class PlayerStatsUnavailable(RuntimeError):
    """Player stats could not be read, or no longer have the shape we parse."""


_cache: dict[int, pl.DataFrame] = {}


def load(season: int) -> pl.DataFrame:
    """One season of weekly player stats, downloaded once per process."""
    if season not in _cache:
        try:
            frame = nfl.load_player_stats(season)
        except Exception as exc:  # noqa: BLE001 - network, upstream, or parse
            raise PlayerStatsUnavailable(
                f"Could not load {season} player stats: {exc}"
            ) from exc

        frame = frame.collect() if isinstance(frame, pl.LazyFrame) else frame

        try:
            check_columns(frame, REQUIRED_COLUMNS, f"{season} player stats")
        except PlayByPlayUnavailable as exc:
            raise PlayerStatsUnavailable(str(exc)) from exc

        frame = frame.filter(pl.col("season_type") == REGULAR_SEASON)

        log.info("Loaded %d player-week rows for %s.", frame.height, season)
        _cache[season] = frame

    return _cache[season]


def season_totals(season: int) -> pl.DataFrame:
    """Sum each player's season, and each team's, in one pass.

    A player's counting stats are summed across every team he played for, so a
    midseason trade does not split him into two half-players. His *share* is
    computed against his primary team -- the one he saw the most targets with
    -- because a share of two different denominators is not a number.
    """
    weekly = load(season)

    per_player = weekly.group_by("player_id").agg(
        player=pl.col("player_display_name").drop_nulls().last(),
        position=pl.col("position").drop_nulls().last(),
        targets=pl.col("targets").fill_null(0).sum(),
        receiving_yards=pl.col("receiving_yards").fill_null(0).sum(),
        carries=pl.col("carries").fill_null(0).sum(),
        rushing_yards=pl.col("rushing_yards").fill_null(0).sum(),
    )

    return per_player.join(_primary_team(weekly), on="player_id", how="left")


def team_totals(season: int) -> pl.DataFrame:
    """Team season targets, the denominator for target share."""
    return (
        load(season)
        .group_by("team")
        .agg(team_targets=pl.col("targets").fill_null(0).sum())
    )


def _primary_team(weekly: pl.DataFrame) -> pl.DataFrame:
    """The team a player did the most with, for players who moved.

    Ranked on targets first and appearances second, so a receiver traded in
    October lands with whoever actually threw to him rather than with whoever
    happened to be alphabetically first.
    """
    return (
        weekly.group_by(["player_id", "team"])
        .agg(
            team_targets=pl.col("targets").fill_null(0).sum(),
            weeks=pl.len(),
        )
        .sort(["player_id", "team_targets", "weeks", "team"], descending=[False, True, True, False])
        .group_by("player_id", maintain_order=True)
        .first()
        .select(["player_id", pl.col("team").alias("production_team")])
    )


def clear_cache() -> None:
    """Drop the memoised frames. Tests use this; a build never needs it."""
    _cache.clear()
