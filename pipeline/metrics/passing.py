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


class WeeklyTargets:
    """Share of team targets, week by week -- the columns beside the season share.

    The week columns belong to a *team*: its last few games, byes skipped. The
    cells belong to a *player*: in each of those weeks, his targets over the
    targets of whichever team he played for that week. In week 1 that is last
    season's weeks for the team he is on now, and a player who moved is
    measured against the team he was actually on -- the same rule the season
    share follows.

    A week he did not play is None, and one he played without a target is 0.0.
    The box score alone cannot tell those apart, which is why the snap feed's
    appearances come in too.
    """

    def __init__(
        self,
        played: dict[tuple[str, int], tuple[str, float]],
        team_weeks: dict[tuple[str, int], float],
    ) -> None:
        self._played = played
        self._team_weeks = team_weeks

    @classmethod
    def from_frames(
        cls, box_scores: pl.DataFrame, appearances: pl.DataFrame
    ) -> WeeklyTargets:
        """Build from `players.weekly_targets` and `snaps.weekly_appearances`."""
        team_weeks: dict[tuple[str, int], float] = {}
        played: dict[tuple[str, int], tuple[str, float]] = {}

        for row in box_scores.iter_rows(named=True):
            key = (row["team"], int(row["week"]))
            targets = float(row["targets"] or 0)
            team_weeks[key] = team_weeks.get(key, 0.0) + targets
            played[(row["player_id"], int(row["week"]))] = (row["team"], targets)

        for row in appearances.iter_rows(named=True):
            # A box-score row already says he played, and for which team.
            played.setdefault((row["player_id"], int(row["week"])), (row["team"], 0.0))

        return cls(played, team_weeks)

    def recent_weeks(self, team: str, n: int = config.RECENT_WEEKS) -> list[int]:
        """The last `n` weeks `team` played, oldest first."""
        weeks = sorted(week for (side, week) in self._team_weeks if side == team)
        return weeks[-n:] if n > 0 else []

    def shares(
        self, player_id: str, weeks: list[int]
    ) -> tuple[list[float | None], float | None]:
        """One cell per week asked for, plus the share across them.

        The across-weeks share sums targets and team targets over the weeks he
        played rather than averaging his weekly shares: 10 of 20 and 2 of 40 is
        20%, not the 27.5% an average of the two shares would claim.
        """
        cells: list[float | None] = []
        mine = teams = 0.0

        for week in weeks:
            played = self._played.get((player_id, week))
            team_total = self._team_weeks.get((played[0], week), 0.0) if played else 0.0
            if not played or team_total <= 0:
                cells.append(None)
                continue
            cells.append(round(played[1] / team_total, 4))
            mine += played[1]
            teams += team_total

        return cells, (round(mine / teams, 4) if teams > 0 else None)


def load_weekly(season: int) -> WeeklyTargets:
    """Pull the two feeds `WeeklyTargets` is built from."""
    return WeeklyTargets.from_frames(
        players_source.weekly_targets(season),
        snaps_source.weekly_appearances(season),
    )


def receivers(
    totals: pl.DataFrame,
    team_targets: pl.DataFrame,
    snap_share: pl.DataFrame,
    dropbacks: dict[str, int],
    current_team: dict[str, str],
    *,
    limit: int = config.RECEIVER_ROWS,
    weekly: WeeklyTargets | None = None,
    usage: pbp_source.ScoringUsage | None = None,
) -> dict[str, list[ReceiverRow]]:
    """Top receivers per team, keyed by the team they play for *now*.

    Production is credited to the team the player earned it with -- his target
    share is a share of that team's targets -- but he is listed under his
    current team. In week 1 those differ for everyone who changed address in
    the offseason; from week 2 they are the same thing.

    `weekly` adds the week-by-week cells, and `usage` the red zone and end
    zone ones. Without either, or if it fails, every row keeps its season
    columns and simply carries none of those.
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
        shown = players[:limit]
        cells = _weekly_cells(weekly, team, shown)
        scoring = scoring_cells(usage, shown)
        table[team] = [
            ReceiverRow(
                player=player["player"],
                role=player["role"],
                target_share=player["target_share"],
                target_rate=player["target_rate"],
                rec_yds_per_game=player["rec_yds_per_game"],
                weekly_share=cells.get(player["player_id"], ([], None))[0],
                l4_share=cells.get(player["player_id"], ([], None))[1],
                **{
                    key: value
                    for key, value in scoring.get(player["player_id"], {}).items()
                    if key in RECEIVER_SCORING_FIELDS
                },
            )
            for player in shown
        ]

    return table


def build(
    stats_season: int,
    roster_season: int,
    weekly: WeeklyTargets | None = None,
) -> dict[str, list[ReceiverRow]]:
    """Pull every feed this module needs and produce the table."""
    return receivers(
        players_source.season_totals(stats_season),
        players_source.team_totals(stats_season),
        snaps_source.offense_share(stats_season),
        pbp_source.team_dropbacks(pbp_source.load(stats_season)),
        snaps_source.current_teams(roster_season),
        weekly=weekly,
        usage=load_scoring(stats_season),
    )


def _weekly_cells(
    weekly: WeeklyTargets | None, team: str, players: list[dict]
) -> dict[str, tuple[list[float | None], float | None]]:
    """Each shown player's week cells, or nothing at all if they cannot be had.

    Guarded on its own because production's hourly build runs this before
    production's plugin can display it: a bug here has to cost the new columns,
    never the season table beside them.
    """
    if weekly is None:
        return {}
    try:
        weeks = weekly.recent_weeks(team)
        return {
            player["player_id"]: weekly.shares(player["player_id"], weeks) for player in players
        }
    except Exception:  # noqa: BLE001 - the season table must survive this
        log.exception("Weekly target share failed for %s; season columns only.", team)
        return {}


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
                "production_team": row["production_team"],
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


#: The scoring-area fields each table carries. Both come out of one
#: `scoring_cells` call, so a share means the same thing in either table.
RECEIVER_SCORING_FIELDS = ("rz_targets", "rz_target_share", "ez_targets", "ez_target_share")
RUSHER_SCORING_FIELDS = ("inside5_carries", "inside5_share")


def load_scoring(stats_season: int) -> pbp_source.ScoringUsage | None:
    """The scoring-area counts, or None if they cannot be had.

    None rather than an exception: the red zone columns are the newest on the
    page, and a failure in them has to cost those columns and nothing else.
    """
    try:
        return pbp_source.scoring_usage(pbp_source.load(stats_season))
    except Exception:  # noqa: BLE001 - the season tables must survive this
        log.exception("Scoring-area counts failed for %s; those columns stay empty.", stats_season)
        return None


def scoring_cells(
    usage: pbp_source.ScoringUsage | None, players: list[dict]
) -> dict[str, dict[str, int | float | None]]:
    """Each player's scoring-area counts and shares, keyed by player id.

    The share is of the team he earned it with, the same rule `target_share`
    follows, so a player traded mid-season is measured against his old
    offense. A player with no scoring-area plays is a zero, not a gap: the
    count was made and he was not in it. A team with none at all has no share
    to give, and gets None.
    """
    if usage is None:
        return {}
    try:
        cells: dict[str, dict[str, int | float | None]] = {}
        for player in players:
            counts = usage.players.get(player["player_id"], (0, 0, 0))
            team = usage.teams.get(player["production_team"], (0, 0, 0))
            shares = [
                round(count / total, 4) if total > 0 else None
                for count, total in zip(counts, team, strict=True)
            ]
            cells[player["player_id"]] = {
                "rz_targets": counts[0],
                "rz_target_share": shares[0],
                "ez_targets": counts[1],
                "ez_target_share": shares[1],
                "inside5_carries": counts[2],
                "inside5_share": shares[2],
            }
        return cells
    except Exception:  # noqa: BLE001 - the season table must survive this
        log.exception("Scoring-area cells failed; those columns stay empty.")
        return {}


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
