"""The passing-game table, and the one column that needs defending.

TGT RATE is a proxy for a statistic we do not license. The tests that matter
most here are the ones that keep it from producing a number it cannot support:
a division by a snap share nobody recorded, or a rate off a single snap.

The other load-bearing case is week 1. Production comes from last season and
the roster comes from this one, and those two facts have to be combined without
either inventing a target share or listing a player who has left.
"""

from __future__ import annotations

import polars as pl
import pytest

from pipeline.metrics.passing import receivers, target_rate

TOTALS_SCHEMA = {
    "player_id": pl.Utf8,
    "player": pl.Utf8,
    "position": pl.Utf8,
    "targets": pl.Int64,
    "receiving_yards": pl.Int64,
    "carries": pl.Int64,
    "rushing_yards": pl.Int64,
    "production_team": pl.Utf8,
}

SNAP_SCHEMA = {
    "player_id": pl.Utf8,
    "snap_share": pl.Float64,
    "games_with_snap": pl.Int64,
    "snap_position": pl.Utf8,
}


def total(player_id, player, **over):
    row = {
        "player_id": player_id,
        "player": player,
        "position": "WR",
        "targets": 0,
        "receiving_yards": 0,
        "carries": 0,
        "rushing_yards": 0,
        "production_team": "SEA",
    }
    row.update(over)
    return row


def totals(*rows):
    return pl.DataFrame(list(rows), schema=TOTALS_SCHEMA)


def team_targets(**teams):
    return pl.DataFrame(
        [{"team": team, "team_targets": n} for team, n in teams.items()],
        schema={"team": pl.Utf8, "team_targets": pl.Int64},
    )


def snaps(*rows):
    return pl.DataFrame(list(rows), schema=SNAP_SCHEMA)


def snap(player_id, share=0.8, games=17, position="WR"):
    return {
        "player_id": player_id,
        "snap_share": share,
        "games_with_snap": games,
        "snap_position": position,
    }


class TestTargetRate:
    def test_it_divides_targets_by_estimated_pass_snaps(self):
        # 100 targets, 80% of 500 dropbacks = 400 estimated routes.
        assert target_rate(100, 0.8, 500) == pytest.approx(0.25)

    def test_a_player_with_no_recorded_snaps_gets_no_rate(self):
        """The alternative is a division by zero, or an enormous rate off one
        snap. Both are worse than an empty cell."""
        assert target_rate(50, None, 500) is None
        assert target_rate(50, 0.0, 500) is None

    def test_a_team_with_no_recorded_dropbacks_gets_no_rate(self):
        assert target_rate(50, 0.8, None) is None
        assert target_rate(50, 0.8, 0) is None

    def test_a_player_with_no_targets_gets_no_rate(self):
        assert target_rate(0, 0.8, 500) is None


class TestTargetShare:
    def test_it_is_a_share_of_the_team_the_player_earned_it_with(self):
        table = receivers(
            totals(total("p1", "Receiver One", targets=100, production_team="SEA")),
            team_targets(SEA=500),
            snaps(snap("p1")),
            {"SEA": 600},
            {"p1": "SEA"},
        )

        assert table["SEA"][0].target_share == pytest.approx(0.20)

    def test_shares_across_a_team_add_up_to_the_team(self):
        table = receivers(
            totals(
                total("p1", "One", targets=200),
                total("p2", "Two", targets=150),
                total("p3", "Three", targets=150),
            ),
            team_targets(SEA=500),
            snaps(snap("p1"), snap("p2"), snap("p3")),
            {"SEA": 600},
            {"p1": "SEA", "p2": "SEA", "p3": "SEA"},
        )

        assert sum(row.target_share for row in table["SEA"]) == pytest.approx(1.0)

    def test_a_team_with_no_recorded_targets_yields_no_share(self):
        table = receivers(
            totals(total("p1", "One", targets=10, production_team="SEA")),
            team_targets(NE=400),
            snaps(snap("p1")),
            {"SEA": 600},
            {"p1": "SEA"},
        )

        assert table["SEA"][0].target_share is None


class TestReceivingYardsPerGame:
    def test_it_divides_by_games_with_a_snap_not_by_weeks_elapsed(self):
        """A player who missed six weeks is a full-time receiver who missed six
        weeks, not a 40-yard-a-game receiver."""
        table = receivers(
            totals(total("p1", "One", targets=60, receiving_yards=800)),
            team_targets(SEA=500),
            snaps(snap("p1", games=10)),
            {"SEA": 600},
            {"p1": "SEA"},
        )

        assert table["SEA"][0].rec_yds_per_game == pytest.approx(80.0)

    def test_a_player_with_no_recorded_games_gets_no_average(self):
        table = receivers(
            totals(total("p1", "One", targets=60, receiving_yards=800)),
            team_targets(SEA=500),
            snaps(),
            {"SEA": 600},
            {"p1": "SEA"},
        )

        assert table["SEA"][0].rec_yds_per_game is None


class TestWhoAppears:
    def test_a_player_who_is_no_longer_on_a_roster_is_dropped(self):
        """He may have had a fine season. He is not playing on Sunday."""
        table = receivers(
            totals(
                total("p1", "Still Here", targets=100),
                total("p2", "Retired", targets=90),
            ),
            team_targets(SEA=500),
            snaps(snap("p1"), snap("p2")),
            {"SEA": 600},
            {"p1": "SEA"},
        )

        assert [row.player for row in table["SEA"]] == ["Still Here"]

    def test_a_player_is_listed_under_the_team_he_plays_for_now(self):
        """Week 1: 2025 production, 2026 jersey. The badge says the numbers are
        last season's; the table says who is on the field."""
        table = receivers(
            totals(total("p1", "Moved On", targets=100, production_team="SEA")),
            team_targets(SEA=500),
            snaps(snap("p1")),
            {"SEA": 600},
            {"p1": "NE"},
        )

        assert "SEA" not in table
        assert table["NE"][0].player == "Moved On"
        # Still a share of the team he actually earned it with.
        assert table["NE"][0].target_share == pytest.approx(0.20)

    def test_a_player_with_no_targets_is_absent_rather_than_zero(self):
        table = receivers(
            totals(total("p1", "One", targets=50), total("p2", "Blocker", targets=0)),
            team_targets(SEA=500),
            snaps(snap("p1"), snap("p2")),
            {"SEA": 600},
            {"p1": "SEA", "p2": "SEA"},
        )

        assert [row.player for row in table["SEA"]] == ["One"]

    def test_the_table_is_capped_and_keeps_the_most_targeted(self):
        table = receivers(
            totals(*[total(f"p{i}", f"P{i}", targets=100 - i) for i in range(8)]),
            team_targets(SEA=1000),
            snaps(*[snap(f"p{i}") for i in range(8)]),
            {"SEA": 600},
            {f"p{i}": "SEA" for i in range(8)},
            limit=5,
        )

        assert [row.player for row in table["SEA"]] == ["P0", "P1", "P2", "P3", "P4"]


class TestRoles:
    def test_roles_are_numbered_within_position(self):
        table = receivers(
            totals(
                total("p1", "Wideout One", position="WR", targets=150),
                total("p2", "Tight End", position="TE", targets=100),
                total("p3", "Wideout Two", position="WR", targets=90),
                total("p4", "The Back", position="RB", targets=50),
            ),
            team_targets(SEA=500),
            snaps(snap("p1"), snap("p2"), snap("p3"), snap("p4")),
            {"SEA": 600},
            {f"p{i}": "SEA" for i in range(1, 5)},
        )

        assert [row.role for row in table["SEA"]] == ["WR1", "TE1", "WR2", "RB1"]

    def test_roles_are_ranked_before_the_table_is_cut(self):
        """A team's WR3 is its third receiver, not the third name that survived
        the five-row cap."""
        table = receivers(
            totals(
                total("p1", "A", position="WR", targets=150),
                total("p2", "B", position="TE", targets=140),
                total("p3", "C", position="TE", targets=130),
                total("p4", "D", position="RB", targets=120),
                total("p5", "E", position="RB", targets=110),
                total("p6", "F", position="WR", targets=100),
            ),
            team_targets(SEA=1000),
            snaps(*[snap(f"p{i}") for i in range(1, 7)]),
            {"SEA": 600},
            {f"p{i}": "SEA" for i in range(1, 7)},
            limit=2,
        )

        assert [row.role for row in table["SEA"]] == ["WR1", "TE1"]

    def test_an_unexpected_position_keeps_its_own_label(self):
        table = receivers(
            totals(total("p1", "The Quarterback", position="QB", targets=3)),
            team_targets(SEA=500),
            snaps(snap("p1", position="QB")),
            {"SEA": 600},
            {"p1": "SEA"},
        )

        assert table["SEA"][0].role == "QB"


class TestOrdering:
    def test_the_table_is_sorted_by_target_share(self):
        table = receivers(
            totals(
                total("p1", "Third", targets=50),
                total("p2", "First", targets=150),
                total("p3", "Second", targets=100),
            ),
            team_targets(SEA=500),
            snaps(snap("p1"), snap("p2"), snap("p3")),
            {"SEA": 600},
            {f"p{i}": "SEA" for i in range(1, 4)},
        )

        assert [row.player for row in table["SEA"]] == ["First", "Second", "Third"]

    def test_a_tie_is_broken_by_name_so_two_runs_agree(self):
        built = (
            totals(total("p1", "Zeta", targets=100), total("p2", "Alpha", targets=100)),
            team_targets(SEA=500),
            snaps(snap("p1"), snap("p2")),
            {"SEA": 600},
            {"p1": "SEA", "p2": "SEA"},
        )

        assert [row.player for row in receivers(*built)["SEA"]] == ["Alpha", "Zeta"]
        assert [row.player for row in receivers(*built)["SEA"]] == ["Alpha", "Zeta"]
