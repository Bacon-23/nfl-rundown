"""The only tests that can catch nflverse schema drift.

Every other test in this suite runs against frames we built ourselves, which is
what makes them fast and deterministic -- and also what makes them blind to the
one failure mode that will actually take a Sunday build down: nflverse renaming
or dropping a column upstream.

These hit the real feeds. They are marked `live` and deselected by default, so
CI stays offline and a GitHub outage at nflverse never turns into a red build.
Run them by hand when bumping the pinned nflreadpy version, and when a build
starts producing dashes nobody can explain:

    pytest -m live
"""

from __future__ import annotations

import pytest

from pipeline.sources import pbp as pbp_source
from pipeline.sources import players as players_source
from pipeline.sources import snaps as snaps_source

pytestmark = pytest.mark.live

#: The most recent season with a full set of completed games. Bump it once the
#: current season finishes; a partial season would make the row-count floors
#: below fire for the wrong reason.
SEASON = 2025


def test_play_by_play_still_has_every_column_we_read():
    frame = pbp_source.load(SEASON)

    assert set(frame.columns) >= pbp_source.REQUIRED_COLUMNS
    assert frame.height > 40_000


def test_snap_counts_still_have_every_column_we_read():
    frame = snaps_source.load(SEASON)

    assert set(frame.columns) >= snaps_source.REQUIRED_COLUMNS
    # `load` has already dropped everyone without an offensive snap, so this
    # floor is against roughly 10k rows rather than the ~25k the raw
    # regular-season file holds -- 20_000 was the raw count, and could never
    # pass. 2022-2025 came in at 9,985 / 10,078 / 10,056 / 10,094, about 18.5
    # players a team-game, which is a starting offense plus its rotation.
    # 9,000 sits under the lowest of those and still catches a season that
    # arrives half-written.
    assert frame.height > 9_000


def test_player_stats_still_have_every_column_we_read():
    frame = players_source.load(SEASON)

    assert set(frame.columns) >= players_source.REQUIRED_COLUMNS
    assert frame.height > 10_000


def test_snap_shares_are_fractions_rather_than_percentages():
    """PFR posts these as percentages; nflverse converts them. If that ever
    changes, every snap share on the page silently becomes 100x too large."""
    shares = snaps_source.load(SEASON)["offense_pct"].drop_nulls()

    assert shares.max() <= 1.0


def test_proe_still_arrives_in_percentage_points():
    """We divide pass_oe by 100. If nflfastR ever ships it as a fraction, that
    division turns +2.9% into +0.029%, which nobody would notice by eye."""
    pass_oe = pbp_source.load(SEASON)["pass_oe"].drop_nulls()

    assert pass_oe.abs().max() > 1.5


def test_the_pfr_to_gsis_map_still_covers_the_skill_positions():
    """Snap share is joined through this map. Measured at 24 unmatched rows of
    6,294 for 2025; a sharp rise means the id map has moved."""
    import polars as pl

    skill = snaps_source.load(SEASON).filter(
        pl.col("position").is_in(["WR", "TE", "RB", "FB"])
    )
    matched = skill.join(
        snaps_source.player_key(SEASON),
        left_on="pfr_player_id",
        right_on="pfr_id",
        how="left",
    )

    unmatched = matched["player_id"].null_count()
    assert unmatched / skill.height < 0.02
