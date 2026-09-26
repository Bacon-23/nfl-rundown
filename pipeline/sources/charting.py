"""Charted pass-rush data: FTN's blitz counts and PFR's pressures, via nflverse.

Play-by-play records what happened on a dropback but not who came after the
quarterback, so the QB tab's pressure and blitz columns come from two charting
services instead. They do not agree on grain, and that decides what the tab
can show:

- **FTN** charts every play and counts the blitzers on it (`n_blitzers`), keyed
  to nflverse's own game and play ids. That joins straight onto play-by-play,
  so a blitz rate *and* a quarterback's line against the blitz both come out
  of it. FTN's data is CC-BY-SA 4.0 and the page credits it.
- **PFR** publishes pressures per quarterback per game, not per play. That is
  enough for a pressure rate, but not for a line under pressure -- and no free
  feed has a play-level pressure flag for 2026. nflverse's participation file
  (`was_pressure`) stops at 2025.

Both run a few days behind the games, so every rate built on them counts only
the dropbacks from games they have charted.
"""

from __future__ import annotations

import logging
from typing import Final

import nflreadpy as nfl
import polars as pl

from pipeline.sources.pbp import REGULAR_SEASON, PlayByPlayUnavailable, check_columns

log = logging.getLogger(__name__)

FTN_COLUMNS: Final[frozenset[str]] = frozenset(
    {"nflverse_game_id", "nflverse_play_id", "n_blitzers"}
)

PFR_COLUMNS: Final[frozenset[str]] = frozenset(
    {"game_id", "game_type", "team", "opponent", "pfr_player_id", "times_pressured"}
)


class ChartingUnavailable(RuntimeError):
    """A charting feed could not be read, or no longer has the shape we parse."""


_blitz_cache: dict[int, pl.DataFrame] = {}
_pressure_cache: dict[int, pl.DataFrame] = {}


def blitzes(season: int) -> pl.DataFrame:
    """FTN's blitzer count per play: `game_id`, `play_id`, `n_blitzers`.

    Renamed and cast to play-by-play's key, so the join is one line wherever
    it is made. FTN charts the regular season and the playoffs alike; the
    play-by-play side of the join is what drops the playoffs.
    """
    if season not in _blitz_cache:
        frame = _load(lambda: nfl.load_ftn_charting(season), f"{season} FTN charting")
        _guard(frame, FTN_COLUMNS, f"{season} FTN charting")

        _blitz_cache[season] = frame.select(
            pl.col("nflverse_game_id").alias("game_id"),
            pl.col("nflverse_play_id").cast(pl.Int64).alias("play_id"),
            pl.col("n_blitzers").cast(pl.Int64),
        ).drop_nulls(["game_id", "play_id"])
        log.info("Loaded %d FTN charted plays for %s.", frame.height, season)

    return _blitz_cache[season]


def pressures(season: int) -> pl.DataFrame:
    """PFR's pressures per quarterback per game, regular season only.

    One row per passer per game: `game_id`, `team`, `opponent`,
    `pfr_player_id`, `times_pressured`. A row means PFR charted that game, so a
    null count is a zero rather than a gap.
    """
    if season not in _pressure_cache:
        frame = _load(
            lambda: nfl.load_pfr_advstats(season, stat_type="pass", summary_level="week"),
            f"{season} PFR advanced passing",
        )
        _guard(frame, PFR_COLUMNS, f"{season} PFR advanced passing")

        _pressure_cache[season] = frame.filter(pl.col("game_type") == REGULAR_SEASON).select(
            "game_id",
            "team",
            "opponent",
            "pfr_player_id",
            pl.col("times_pressured").fill_null(0).cast(pl.Float64),
        )
        log.info("Loaded %d PFR passer-games for %s.", frame.height, season)

    return _pressure_cache[season]


def _load(fetch, what: str) -> pl.DataFrame:
    try:
        frame = fetch()
    except Exception as exc:  # noqa: BLE001 - network, upstream, or parse
        raise ChartingUnavailable(f"Could not load {what}: {exc}") from exc
    return frame.collect() if isinstance(frame, pl.LazyFrame) else frame


def _guard(frame: pl.DataFrame, required: frozenset[str], what: str) -> None:
    try:
        check_columns(frame, required, what)
    except PlayByPlayUnavailable as exc:
        raise ChartingUnavailable(str(exc)) from exc


def clear_cache() -> None:
    """Drop the memoised frames. Tests use this; a build never needs it."""
    _blitz_cache.clear()
    _pressure_cache.clear()
