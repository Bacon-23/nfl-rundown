"""Red zone targets, end zone targets, carries inside the 5.

Every one of these is a count a reader can check against Pro Football
Reference, so the tests are about the edges: which yard line is in, which play
is a target, which run is a carry. A scramble counted as a goal-line carry, or
a two-point try counted as a target, is the kind of wrong that survives until
someone lines the page up against a box score.
"""

from __future__ import annotations

import polars as pl
import pytest

from pipeline.metrics.passing import receivers
from pipeline.metrics.rushing import backs
from pipeline.sources.pbp import (
    SCORING_COLUMNS,
    PlayByPlayUnavailable,
    ScoringUsage,
    scoring_usage,
)
from pipeline.tests.test_passing import snap, snaps, team_targets, total, totals

SCHEMA = {
    "season_type": pl.Utf8,
    "posteam": pl.Utf8,
    "play_type": pl.Utf8,
    "qb_kneel": pl.Int64,
    "yardline_100": pl.Float64,
    "air_yards": pl.Float64,
    "receiver_player_id": pl.Utf8,
    "rusher_player_id": pl.Utf8,
    "sack": pl.Int64,
    "qb_scramble": pl.Int64,
    "two_point_attempt": pl.Int64,
}


def target(receiver="wr1", yardline=10, air=5, **over):
    """A completed-or-not pass to `receiver`, from `yardline` yards out."""
    row = {
        "season_type": "REG",
        "posteam": "SEA",
        "play_type": "pass",
        "qb_kneel": 0,
        "yardline_100": yardline,
        "air_yards": air,
        "receiver_player_id": receiver,
        "rusher_player_id": None,
        "sack": 0,
        "qb_scramble": 0,
        "two_point_attempt": 0,
    }
    row.update(over)
    return row


def carry(rusher="rb1", yardline=3, **over):
    """A designed run by `rusher` from `yardline` yards out."""
    row = target(receiver=None, yardline=yardline, air=None)
    row.update(play_type="run", rusher_player_id=rusher)
    row.update(over)
    return row


def usage_of(*plays):
    return scoring_usage(pl.DataFrame(list(plays), schema=SCHEMA))


def test_the_fixture_schema_is_exactly_what_the_function_checks():
    assert set(SCHEMA) == SCORING_COLUMNS


class TestRedZone:
    def test_the_20_is_in_and_the_21_is_out(self):
        usage = usage_of(target(yardline=20, air=2), target(yardline=21, air=2))

        assert usage.players["wr1"][0] == 1

    def test_the_team_total_is_the_denominator(self):
        usage = usage_of(target("wr1"), target("wr1"), target("te1"))

        assert usage.teams["SEA"][0] == 3


class TestEndZone:
    def test_air_yards_reaching_the_goal_line_is_an_end_zone_target(self):
        usage = usage_of(target(yardline=8, air=8))

        assert usage.players["wr1"][1] == 1

    def test_a_yard_short_is_not(self):
        usage = usage_of(target(yardline=8, air=7))

        assert usage.players["wr1"][1] == 0

    def test_no_recorded_air_yards_is_not_guessed_at(self):
        usage = usage_of(target(yardline=8, air=None))

        assert usage.players["wr1"] == (1, 0, 0)

    def test_a_deep_shot_from_midfield_is_an_end_zone_target_but_not_a_red_zone_one(self):
        usage = usage_of(target(yardline=45, air=50))

        assert usage.players["wr1"] == (0, 1, 0)

    def test_a_long_pass_that_falls_short_of_the_goal_line_is_neither(self):
        usage = usage_of(target(yardline=45, air=30))

        assert "wr1" not in usage.players


class TestInsideTheFive:
    def test_the_5_is_in_and_the_6_is_out(self):
        usage = usage_of(carry(yardline=5), carry(yardline=6))

        assert usage.players["rb1"][2] == 1

    def test_a_scramble_is_not_a_carry(self):
        usage = usage_of(carry("qb1", qb_scramble=1))

        assert "qb1" not in usage.players

    def test_a_kneel_is_not_a_carry(self):
        usage = usage_of(carry("qb1", qb_kneel=1))

        assert "qb1" not in usage.players

    def test_a_sneak_counts_toward_the_team(self):
        """The back did not get that carry, and his share should say so."""
        usage = usage_of(carry("rb1"), carry("qb1"))

        assert usage.teams["SEA"][2] == 2


class TestWhatIsNotAPlay:
    @pytest.mark.parametrize(
        "play",
        [
            target(sack=1),
            target(two_point_attempt=1, yardline=2, air=2),
            carry(two_point_attempt=1),
            target(play_type="no_play"),
            target(season_type="POST"),
            target(posteam=None),
            target(receiver=None),
        ],
        ids=[
            "sack",
            "two-point pass",
            "two-point run",
            "penalty",
            "postseason",
            "no offense",
            "no receiver",
        ],
    )
    def test_it_is_not_counted(self, play):
        usage = usage_of(play)

        assert usage.players == {}
        assert usage.teams == {}


def test_a_moved_column_fails_by_name():
    frame = pl.DataFrame([target()], schema=SCHEMA).drop("air_yards")

    with pytest.raises(PlayByPlayUnavailable, match="air_yards"):
        scoring_usage(frame)


# ---------------------------------------------------------------------------
# The tables
# ---------------------------------------------------------------------------


def receiver_table(usage, *rows, current=None):
    ids = [row["player_id"] for row in rows]
    return receivers(
        totals(*rows),
        team_targets(SEA=500, KC=500),
        snaps(*(snap(pid) for pid in ids)),
        {"SEA": 600, "KC": 600},
        current or dict.fromkeys(ids, "SEA"),
        usage=usage,
    )


class TestReceiverColumns:
    def test_count_and_share_of_the_team(self):
        usage = ScoringUsage(
            players={"wr1": (6, 2, 0)},
            teams={"SEA": (24, 8, 10)},
        )

        row = receiver_table(usage, total("wr1", "Receiver", targets=100))["SEA"][0]

        assert (row.rz_targets, row.rz_target_share) == (6, 0.25)
        assert (row.ez_targets, row.ez_target_share) == (2, 0.25)

    def test_a_receiver_with_no_red_zone_looks_is_a_zero_not_a_gap(self):
        usage = ScoringUsage(players={}, teams={"SEA": (24, 8, 10)})

        row = receiver_table(usage, total("wr1", "Receiver", targets=100))["SEA"][0]

        assert (row.rz_targets, row.rz_target_share) == (0, 0.0)

    def test_a_team_with_no_red_zone_targets_has_no_share_to_give(self):
        usage = ScoringUsage(players={}, teams={})

        row = receiver_table(usage, total("wr1", "Receiver", targets=100))["SEA"][0]

        assert row.rz_targets == 0
        assert row.rz_target_share is None

    def test_a_traded_receiver_is_measured_against_the_team_he_earned_it_with(self):
        """Listed under his new team, shared against his old one -- the same
        rule target share follows."""
        usage = ScoringUsage(
            players={"wr1": (5, 0, 0)},
            teams={"KC": (20, 4, 8), "SEA": (50, 10, 10)},
        )

        table = receiver_table(
            usage,
            total("wr1", "Moved", targets=100, production_team="KC"),
            current={"wr1": "SEA"},
        )

        assert table["SEA"][0].rz_target_share == 0.25

    def test_without_the_counts_the_columns_are_empty_and_the_rest_stands(self):
        row = receiver_table(None, total("wr1", "Receiver", targets=100))["SEA"][0]

        assert row.target_share == pytest.approx(0.20)
        assert row.rz_targets is None
        assert row.rz_target_share is None

    def test_the_receiver_row_carries_no_carries(self):
        usage = ScoringUsage(players={"wr1": (6, 2, 3)}, teams={"SEA": (24, 8, 10)})

        row = receiver_table(usage, total("wr1", "Receiver", targets=100))["SEA"][0]

        assert "inside5_carries" not in row.model_dump()


class TestRusherColumns:
    def rows(self, usage):
        return backs(
            totals(total("rb1", "Back", position="RB", carries=200)),
            team_targets(SEA=500),
            snaps(snap("rb1", position="RB")),
            {"SEA": 600},
            {"rb1": "SEA"},
            usage=usage,
        )["SEA"]

    def test_count_and_share_of_the_team(self):
        usage = ScoringUsage(players={"rb1": (1, 0, 6)}, teams={"SEA": (24, 8, 10)})

        row = self.rows(usage)[0]

        assert (row.inside5_carries, row.inside5_share) == (6, 0.6)

    def test_without_the_counts_the_column_is_empty_and_the_rest_stands(self):
        row = self.rows(None)[0]

        assert row.snap_share == pytest.approx(0.8)
        assert row.inside5_carries is None
        assert row.inside5_share is None
