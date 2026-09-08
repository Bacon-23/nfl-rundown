"""The passing-game table: target share, TGT RATE, and receiving yards.

Read the note on `target_rate()` before publishing anything from this module.
The mockup labels its third column TPRR; what we compute is not TPRR, and the
distance between the two is the difference between a defensible number and one
we would have to retract.

Every rate here is a fraction, matching `efficiency.py`.
"""

from __future__ import annotations

import logging

import polars as pl

from pipeline import config
from pipeline.schema import ReceiverRow
from pipeline.sources import pbp as pbp_source
from pipeline.sources import players as players_source
from pipeline.sources import snaps as snaps_source

log = logging.getLogger(__name__)

#: Positions that appear in a passing-game table. A back who catches 60 balls
#: belongs here; a lineman who caught one tackle-eligible pass does not.
RECEIVING_POSITIONS = ("WR", "TE", "RB", "FB")


def target_rate(
    targets: float | None,
    snap_share: float | None,
    team_dropbacks: int | None,
) -> float | None:
    """Targets per *estimated* pass snap. A proxy for TPRR -- never call it TPRR.

    True targets per route run needs charted route data from PFF, FTN, or SIS,
    which we do not license. This estimates the routes denominator as the
    player's offensive snap share times his team's dropbacks. It correlates
    well and ranks players in a similar order, but it is a different statistic:
    a receiver who sits out passing downs looks better here than a real
    routes-run measure would show him.

    So the column is labeled TGT RATE, the tooltip says what it is, and the
    computation is this one function -- licensing a real feed later replaces
    the body and touches nothing else.

    Returns None rather than a number when the denominator is unusable. A
    player with no recorded snaps would otherwise divide by zero, or worse,
    post an enormous rate off a single snap.
    """
    if not targets or not snap_share or not team_dropbacks:
        return None

    estimated_pass_snaps = snap_share * team_dropbacks
    if estimated_pass_snaps <= 0:
        return None

    return round(targets / estimated_pass_snaps, 4)


def receivers(
    totals: pl.DataFrame,
    team_targets: pl.DataFrame,
    snap_share: pl.DataFrame,
    dropbacks: dict[str, int],
    current_team: dict[str, str],
    *,
    limit: int = config.RECEIVER_ROWS,
) -> dict[str, list[ReceiverRow]]:
    """Top receivers per team, keyed by the team they play for *now*.

    Production is credited to the team the player earned it with -- his target
    share is a share of that team's targets -- but he is listed under his
    current team. In week 1 those differ for everyone who changed address in
    the offseason; from week 2 they are the same thing.
    """
    rows = player_rows(totals, team_targets, snap_share, dropbacks, current_team)

    by_team: dict[str, list[dict]] = {}
    for row in rows:
        if row["targets"] <= 0:
            continue
        by_team.setdefault(row["current_team"], []).append(row)

    table: dict[str, list[ReceiverRow]] = {}
    for team, players in by_team.items():
        players.sort(key=_target_order)
        _assign_roles(players)
        table[team] = [
            ReceiverRow(
                player=player["player"],
                role=player["role"],
                target_share=player["target_share"],
                target_rate=player["target_rate"],
                rec_yds_per_game=player["rec_yds_per_game"],
            )
            for player in players[:limit]
        ]

    return table


def build(stats_season: int, roster_season: int) -> dict[str, list[ReceiverRow]]:
    """Pull every feed this module needs and produce the table."""
    return receivers(
        players_source.season_totals(stats_season),
        players_source.team_totals(stats_season),
        snaps_source.offense_share(stats_season),
        pbp_source.team_dropbacks(pbp_source.load(stats_season)),
        snaps_source.current_teams(roster_season),
    )


# ---------------------------------------------------------------------------
# Shared with rushing.py. Both tables describe the same players from the same
# feeds, and computing target share two different ways would be a bug waiting.
# ---------------------------------------------------------------------------


def player_rows(
    totals: pl.DataFrame,
    team_targets: pl.DataFrame,
    snap_share: pl.DataFrame,
    dropbacks: dict[str, int],
    current_team: dict[str, str],
) -> list[dict]:
    """Flatten the feeds into one dict per player who is on a roster today.

    A player absent from the current roster is dropped outright. He may have
    had a fine season; he is not playing on Sunday, and a table of people who
    will not take the field is worse than a short table.
    """
    frame = totals.join(snap_share, on="player_id", how="left").join(
        team_targets, left_on="production_team", right_on="team", how="left"
    )

    rows: list[dict] = []
    for row in frame.iter_rows(named=True):
        team = current_team.get(row["player_id"])
        if team is None:
            continue

        targets = float(row["targets"] or 0)
        carries = float(row["carries"] or 0)
        team_total = float(row["team_targets"] or 0)
        games = int(row["games_with_snap"] or 0)

        rows.append(
            {
                "player_id": row["player_id"],
                "player": row["player"] or row["player_id"],
                "position": row["position"] or row.get("snap_position"),
                "current_team": team,
                "targets": targets,
                "carries": carries,
                "snap_share": _round(row["snap_share"], 3),
                "games_with_snap": games,
                "target_share": (
                    round(targets / team_total, 4) if team_total > 0 else None
                ),
                "target_rate": target_rate(
                    targets, row["snap_share"], dropbacks.get(row["production_team"])
                ),
                # Per *game played*, not per week elapsed. A player who missed
                # six weeks is not a 20-yard receiver.
                "rec_yds_per_game": (
                    round(float(row["receiving_yards"] or 0) / games, 1)
                    if games
                    else None
                ),
                "rush_att_per_game": round(carries / games, 1) if games else None,
                "yards_per_att": (
                    round(float(row["rushing_yards"] or 0) / carries, 1)
                    if carries
                    else None
                ),
            }
        )

    return rows


def _target_order(player: dict) -> tuple:
    """Most-targeted first, with the player's name breaking ties reproducibly."""
    return (-(player["target_share"] or 0.0), -player["targets"], player["player"])


def _assign_roles(players: list[dict]) -> None:
    """Label WR1, TE1, RB1 by rank within position, over the whole team.

    Ranked before the five-row cut, so a team's WR3 is its third receiver
    rather than the third name that happened to survive the table.
    """
    seen: dict[str, int] = {}
    for player in players:
        position = (player["position"] or "").upper()
        if position in RECEIVING_POSITIONS:
            seen[position] = seen.get(position, 0) + 1
            player["role"] = f"{position}{seen[position]}"
        else:
            player["role"] = position or None


def _round(value, places: int):
    return None if value is None else round(float(value), places)
