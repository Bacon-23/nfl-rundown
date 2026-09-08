"""Team efficiency: pass and rush rate, PROE, pace, plays per game, EPA.

Computed for all 32 teams in one pass, because EPA rank is a league-wide
statement and cannot be worked out from the two teams in a matchup.

Units, stated once and asserted in the tests: **every rate is a fraction**.
Pass rate 0.542 means 54.2%, and PROE 0.029 means +2.9%. nflfastR publishes
`pass_oe` in percentage points, so it is divided by 100 on the way in. One
convention means the renderer needs one percent formatter, and it means anyone
recomputing our numbers from the JSON knows which units they are holding.
"""

from __future__ import annotations

import logging

import polars as pl

from pipeline import config
from pipeline.schema import TeamEfficiency
from pipeline.sources import pbp as pbp_source

log = logging.getLogger(__name__)


def team_efficiency(frame: pl.DataFrame) -> dict[str, TeamEfficiency]:
    """One row per team that ran an offensive play, keyed by abbreviation."""
    plays = pbp_source.offensive_plays(frame)

    if plays.height == 0:
        log.warning("No offensive plays in the frame; efficiency table is empty.")
        return {}

    volume = plays.group_by("posteam").agg(
        pass_rate=(pl.col("play_type") == "pass").mean(),
        epa_per_play=pl.col("epa").mean(),
        plays=pl.len(),
        games=pl.col("game_id").n_unique(),
    )

    table = (
        volume.join(_proe(frame), on="posteam", how="left")
        .join(_pace(plays), on="posteam", how="left")
        .with_columns(plays_per_game=pl.col("plays") / pl.col("games"))
        # Ranked best-first, ties broken on the team abbreviation so two teams
        # with identical EPA do not swap ranks between runs.
        .sort(["epa_per_play", "posteam"], descending=[True, False], nulls_last=True)
        .with_row_index("epa_rank", offset=1)
    )

    return {
        row["posteam"]: TeamEfficiency(
            team=row["posteam"],
            pass_rate=_round(row["pass_rate"], 4),
            rush_rate=_round(_complement(row["pass_rate"]), 4),
            proe=_round(row["proe"], 4),
            pace=_round(row["pace"], 1),
            plays_per_game=_round(row["plays_per_game"], 1),
            epa_per_play=_round(row["epa_per_play"], 3),
            epa_rank=None if row["epa_per_play"] is None else int(row["epa_rank"]),
        )
        for row in table.iter_rows(named=True)
    }


def _proe(frame: pl.DataFrame) -> pl.DataFrame:
    """Pass rate over expected, from nflfastR's own model.

    Averaged over plays where `pass_oe` is populated rather than over the
    offensive-play filter above: the model declines to score some snaps, and
    substituting a zero for "no opinion" would drag every team toward neutral.
    """
    scored = frame.filter(
        (pl.col("season_type") == pbp_source.REGULAR_SEASON)
        & pl.col("posteam").is_not_null()
        & pl.col("pass_oe").is_not_null()
    )
    return scored.group_by("posteam").agg(
        # Percentage points upstream, fractions here.
        proe=pl.col("pass_oe").mean() / 100.0
    )


def _pace(plays: pl.DataFrame) -> pl.DataFrame:
    """Seconds between snaps, in neutral game states.

    "Pace" has no single industry definition, so this one is spelled out:
    the elapsed game clock between consecutive plays of the same drive, on
    first and second down, with win probability between 0.20 and 0.80.

    Three things are excluded and each matters. Gaps across drives, because
    the other team had the ball in between. Gaps at or below zero, which are
    clock-stoppage artifacts. And gaps beyond a minute, which are timeouts,
    injuries, reviews, and TV breaks -- the broadcast rather than the huddle.
    """
    low, high = config.PACE_WP_RANGE

    gaps = (
        plays.sort(["game_id", "fixed_drive", "play_id"])
        .with_columns(
            gap=(
                pl.col("game_seconds_remaining").shift(1)
                - pl.col("game_seconds_remaining")
            ).over(["game_id", "fixed_drive"])
        )
        .filter(
            pl.col("gap").is_not_null()
            & (pl.col("gap") > 0)
            & (pl.col("gap") <= config.PACE_MAX_GAP_SECONDS)
            & pl.col("down").is_in(list(config.PACE_DOWNS))
            & pl.col("wp").is_between(low, high)
        )
    )

    return gaps.group_by("posteam").agg(pace=pl.col("gap").mean())


def _complement(pass_rate: float | None) -> float | None:
    """Rush rate is whatever pass rate is not, over the same filtered plays."""
    return None if pass_rate is None else 1.0 - pass_rate


def games_played(frame: pl.DataFrame) -> dict[str, int]:
    """Completed regular-season games per team, for the sample badge.

    Taken from the play-by-play rather than from the schedule so it can never
    disagree with the numbers it labels: if a game is missing from the feed the
    badge says so, instead of claiming a sample the table does not have.
    """
    plays = pbp_source.offensive_plays(frame)
    tally = plays.group_by("posteam").agg(games=pl.col("game_id").n_unique())
    return {row["posteam"]: int(row["games"]) for row in tally.iter_rows(named=True)}


def _round(value: float | None, places: int) -> float | None:
    return None if value is None else round(float(value), places)
