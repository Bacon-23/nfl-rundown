"""Snap counts from Pro Football Reference, via nflverse.

Snap share is the honest denominator behind both player tables: it separates a
receiver who was on the field for every dropback from one who rotated in, and
it is the whole of the running-back workload column.

PFR identifies players by its own id, not by the gsis id the rest of nflverse
uses, so `player_key()` maps between them through the season's roster file.
Over 2025's skill positions that mapping covers all but 24 of 6,294 rows, and
the misses are deep reserves who would never reach a five-row table.
"""

from __future__ import annotations

import logging
from typing import Final

import nflreadpy as nfl
import polars as pl

from pipeline.sources.pbp import REGULAR_SEASON, PlayByPlayUnavailable, check_columns
from pipeline.sources.team_map import to_abbr

log = logging.getLogger(__name__)

REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "season",
        "week",
        "game_type",
        "game_id",
        "player",
        "pfr_player_id",
        "position",
        "team",
        "offense_snaps",
        "offense_pct",
    }
)


class SnapCountsUnavailable(RuntimeError):
    """Snap counts could not be read, or no longer have the shape we parse."""


_cache: dict[int, pl.DataFrame] = {}


def load(season: int) -> pl.DataFrame:
    """Regular-season snap counts for one season, downloaded once per process.

    `offense_pct` arrives as a fraction between 0 and 1, which is the
    convention this package stores every rate in.
    """
    if season not in _cache:
        try:
            frame = nfl.load_snap_counts(season)
        except Exception as exc:  # noqa: BLE001 - network, upstream, or parse
            raise SnapCountsUnavailable(
                f"Could not load {season} snap counts: {exc}"
            ) from exc

        frame = frame.collect() if isinstance(frame, pl.LazyFrame) else frame

        try:
            check_columns(frame, REQUIRED_COLUMNS, f"{season} snap counts")
        except PlayByPlayUnavailable as exc:
            raise SnapCountsUnavailable(str(exc)) from exc

        frame = frame.filter(
            (pl.col("game_type") == REGULAR_SEASON)
            & pl.col("offense_snaps").fill_null(0).gt(0)
        )

        log.info("Loaded %d offensive snap rows for %s.", frame.height, season)
        _cache[season] = frame

    return _cache[season]


def offense_share(season: int) -> pl.DataFrame:
    """Per-player mean offensive snap share, keyed by gsis id.

    Averaged over games the player actually appeared in. A player who missed
    six weeks is not a 40%-snap player; he is a full-time player who missed six
    weeks, and games he never dressed for should not say otherwise.
    """
    frame = load(season).join(
        player_key(season), left_on="pfr_player_id", right_on="pfr_id", how="left"
    )

    unmatched = frame.filter(pl.col("player_id").is_null()).height
    if unmatched:
        log.debug(
            "%d of %d snap rows have no gsis id for %s; those players lose their "
            "snap share only.",
            unmatched,
            frame.height,
            season,
        )

    return (
        frame.filter(pl.col("player_id").is_not_null())
        .group_by("player_id")
        .agg(
            snap_share=pl.col("offense_pct").mean(),
            games_with_snap=pl.col("game_id").n_unique(),
            snap_position=pl.col("position").drop_nulls().first(),
        )
    )


def player_key(season: int) -> pl.DataFrame:
    """PFR id to gsis id, from that season's roster file.

    Rows without both ids are dropped rather than guessed at: an unmatched
    player costs one snap-share cell, while a wrong match would put someone
    else's workload under his name.
    """
    roster = _roster(season)
    return (
        roster.select(
            pfr_id=pl.col("pfr_id"),
            player_id=pl.col("gsis_id"),
        )
        .drop_nulls()
        .unique(subset=["pfr_id"], keep="first")
    )


def current_teams(season: int) -> dict[str, str]:
    """gsis id to the team a player is on *now*.

    Week 1 publishes last season's production, and between February and
    September a third of the league changes address. Attributing 2025 numbers
    to 2025 rosters would list players who have since left; this map is what
    puts them under the jersey they will actually wear on Sunday. The badge on
    the module says the production is last year's.

    Team codes go through `to_abbr` rather than being trusted as they arrive:
    the roster file is the one nflverse feed that codes Arizona "AZ", and an
    unmapped code does not raise an error here -- it quietly publishes a team
    with no players at all.
    """
    roster = _roster(season)

    if "status" in roster.columns:
        # ACT is the active roster; everything else is cut, waived, practice
        # squad, or a reserve list. A player on PUP or IR is not taking the
        # field on Sunday, and the injury table is where he belongs.
        roster = roster.filter(pl.col("status") == "ACT")

    rows = roster.select(["gsis_id", "team"]).drop_nulls()
    return {
        row["gsis_id"]: to_abbr(row["team"]) for row in rows.iter_rows(named=True)
    }


_roster_cache: dict[int, pl.DataFrame] = {}


def _roster(season: int) -> pl.DataFrame:
    if season not in _roster_cache:
        try:
            frame = nfl.load_rosters(season)
        except Exception as exc:  # noqa: BLE001 - network, upstream, or parse
            raise SnapCountsUnavailable(
                f"Could not load the {season} roster: {exc}"
            ) from exc
        _roster_cache[season] = (
            frame.collect() if isinstance(frame, pl.LazyFrame) else frame
        )
    return _roster_cache[season]


def clear_cache() -> None:
    """Drop the memoised frames. Tests use this; a build never needs it."""
    _cache.clear()
    _roster_cache.clear()
