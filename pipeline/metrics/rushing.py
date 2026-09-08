"""Running back workload: snap share, carries, passing-game role, yards per carry.

The four columns answer one question between them -- how much of this offense
runs through this back. Snap share leads because it is the one that survives a
game script: a back with 14 carries in a blowout and a back with 14 carries in
a one-score game are not the same player, and their snap shares say so.

Built from the same flattened player rows as `passing.py`, deliberately. Target
share appears in both tables and must mean the same thing in each.
"""

from __future__ import annotations

import logging

import polars as pl

from pipeline import config
from pipeline.metrics.passing import player_rows
from pipeline.schema import RusherRow
from pipeline.sources import pbp as pbp_source
from pipeline.sources import players as players_source
from pipeline.sources import snaps as snaps_source

log = logging.getLogger(__name__)

#: Fullbacks are included: on the teams that still use one, he is a real part
#: of the backfield workload, and leaving him out understates the committee.
RUSHING_POSITIONS = ("RB", "FB")


def backs(
    totals: pl.DataFrame,
    team_targets: pl.DataFrame,
    snap_share: pl.DataFrame,
    dropbacks: dict[str, int],
    current_team: dict[str, str],
    *,
    limit: int = config.RUSHER_ROWS,
    min_att_per_game: float = config.RUSHER_MIN_ATT_PER_GAME,
) -> dict[str, list[RusherRow]]:
    """Top backs per team, keyed by the team they play for *now*.

    A quarterback can lead his team in carries; he is not a running back, so
    the position filter runs before the sort rather than after it. And a back
    who touches the ball once a month is not a workload, however many snaps he
    plays -- see `RUSHER_MIN_ATT_PER_GAME`.
    """
    rows = player_rows(totals, team_targets, snap_share, dropbacks, current_team)

    by_team: dict[str, list[dict]] = {}
    for row in rows:
        position = (row["position"] or "").upper()
        if position not in RUSHING_POSITIONS or row["carries"] <= 0:
            continue
        # A back with no snap data has no per-game rate to test, and dropping
        # him for that would punish a missing join rather than a missing role.
        rate = row["rush_att_per_game"]
        if rate is not None and rate < min_att_per_game:
            continue
        by_team.setdefault(row["current_team"], []).append(row)

    table: dict[str, list[RusherRow]] = {}
    for team, players in by_team.items():
        players.sort(key=_workload_order)
        table[team] = [
            RusherRow(
                player=player["player"],
                snap_share=player["snap_share"],
                rush_att_per_game=player["rush_att_per_game"],
                target_share=player["target_share"],
                yards_per_att=player["yards_per_att"],
            )
            for player in players[:limit]
        ]

    return table


def build(stats_season: int, roster_season: int) -> dict[str, list[RusherRow]]:
    """Pull every feed this module needs and produce the table."""
    return backs(
        players_source.season_totals(stats_season),
        players_source.team_totals(stats_season),
        snaps_source.offense_share(stats_season),
        pbp_source.team_dropbacks(pbp_source.load(stats_season)),
        snaps_source.current_teams(roster_season),
    )


def _workload_order(player: dict) -> tuple:
    """Most snaps first, then most carries, then name.

    A back with no snap-count match sorts last rather than first: an unknown
    share is not a zero one, but it cannot outrank a measured one either.
    """
    return (
        -(player["snap_share"] or 0.0),
        -player["carries"],
        player["player"],
    )
