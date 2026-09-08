"""Running back workload.

Two things distinguish this table from the passing one. It is sorted on snap
share rather than volume, because that is the column that survives a game
script. And it is position-filtered before the sort: a scrambling quarterback
can lead his team in carries without being part of the backfield.
"""

from __future__ import annotations

import pytest

from pipeline.metrics.rushing import backs
from pipeline.tests.test_passing import snap, snaps, team_targets, total, totals


def rushers(*rows, snap_rows=(), current=None, dropbacks=None, **kwargs):
    """Run the module over one team's worth of hand-built rows."""
    ids = [row["player_id"] for row in rows]
    return backs(
        totals(*rows),
        team_targets(SEA=500),
        snaps(*(snap_rows or [snap(pid, position="RB") for pid in ids])),
        dropbacks or {"SEA": 600},
        current or dict.fromkeys(ids, "SEA"),
        **kwargs,
    )


class TestWhoAppears:
    def test_a_quarterback_is_not_a_running_back(self):
        """He can still lead the team in carries."""
        table = rushers(
            total("p1", "The Back", position="RB", carries=200),
            total("p2", "The Quarterback", position="QB", carries=250),
        )

        assert [row.player for row in table["SEA"]] == ["The Back"]

    def test_a_fullback_is_part_of_the_backfield(self):
        table = rushers(
            total("p1", "Halfback", position="RB", carries=200),
            total("p2", "Fullback", position="FB", carries=20),
        )

        assert [row.player for row in table["SEA"]] == ["Halfback", "Fullback"]

    def test_a_back_who_never_carried_the_ball_is_absent_rather_than_zero(self):
        table = rushers(
            total("p1", "Starter", position="RB", carries=200),
            total("p2", "Practice Squad", position="RB", carries=0),
        )

        assert [row.player for row in table["SEA"]] == ["Starter"]

    def test_a_fullback_who_barely_carries_is_not_a_workload(self):
        """Over 2025 the third row was otherwise Reggie Gilliam and Kyle
        Juszczyk at a tenth of a carry a game. Real players, real snap shares,
        not a rushing workload."""
        table = rushers(
            total("p1", "Feature Back", position="RB", carries=200),
            total("p2", "Blocking Back", position="FB", carries=2),
            snap_rows=[
                snap("p1", share=0.6, games=17, position="RB"),
                snap("p2", share=0.45, games=17, position="FB"),
            ],
        )

        assert [row.player for row in table["SEA"]] == ["Feature Back"]

    def test_a_committee_back_clears_the_floor(self):
        """The floor exists to drop fullbacks, not to hide a real committee."""
        table = rushers(
            total("p1", "Lead", position="RB", carries=170),
            total("p2", "Change Of Pace", position="RB", carries=51),
            snap_rows=[
                snap("p1", share=0.6, games=17, position="RB"),
                snap("p2", share=0.3, games=17, position="RB"),
            ],
        )

        assert [row.player for row in table["SEA"]] == ["Lead", "Change Of Pace"]

    def test_a_back_with_no_snap_data_is_not_dropped_by_the_floor(self):
        """He has no per-game rate to test. Dropping him would punish a missing
        join rather than a missing role."""
        table = rushers(
            total("p1", "Measured", position="RB", carries=200),
            total("p2", "Unmatched", position="RB", carries=150),
            snap_rows=[snap("p1", share=0.6, position="RB")],
        )

        assert "Unmatched" in [row.player for row in table["SEA"]]

    def test_a_back_who_left_the_roster_is_dropped(self):
        table = rushers(
            total("p1", "Still Here", position="RB", carries=150),
            total("p2", "Gone", position="RB", carries=140),
            current={"p1": "SEA"},
        )

        assert [row.player for row in table["SEA"]] == ["Still Here"]

    def test_the_table_is_capped(self):
        rows = [
            total(f"p{i}", f"Back {i}", position="RB", carries=100 - i)
            for i in range(5)
        ]
        table = rushers(
            *rows,
            snap_rows=[snap(f"p{i}", share=0.9 - i / 10, position="RB") for i in range(5)],
            limit=3,
        )

        assert [row.player for row in table["SEA"]] == ["Back 0", "Back 1", "Back 2"]


class TestOrdering:
    def test_the_lead_back_is_the_one_on_the_field_most(self):
        """Snap share over volume: 14 carries in a blowout and 14 carries in a
        one-score game are not the same workload."""
        table = rushers(
            total("p1", "Committee", position="RB", carries=200),
            total("p2", "Three Down", position="RB", carries=180),
            snap_rows=[
                snap("p1", share=0.40, position="RB"),
                snap("p2", share=0.75, position="RB"),
            ],
        )

        assert [row.player for row in table["SEA"]] == ["Three Down", "Committee"]

    def test_a_back_with_no_snap_data_sorts_last_not_first(self):
        """An unknown share is not a zero one, but it cannot outrank a measured
        one either."""
        table = rushers(
            total("p1", "Unmatched", position="RB", carries=250),
            total("p2", "Measured", position="RB", carries=100),
            snap_rows=[snap("p2", share=0.5, position="RB")],
        )

        assert [row.player for row in table["SEA"]] == ["Measured", "Unmatched"]
        assert table["SEA"][1].snap_share is None


class TestColumns:
    def test_carries_are_averaged_over_games_played(self):
        table = rushers(
            total("p1", "Back", position="RB", carries=150),
            snap_rows=[snap("p1", share=0.6, games=10, position="RB")],
        )

        assert table["SEA"][0].rush_att_per_game == pytest.approx(15.0)

    def test_yards_per_attempt_divides_by_attempts_not_by_games(self):
        table = rushers(
            total("p1", "Back", position="RB", carries=200, rushing_yards=900),
        )

        assert table["SEA"][0].yards_per_att == pytest.approx(4.5)

    def test_the_passing_game_role_is_the_same_share_the_other_table_shows(self):
        table = rushers(
            total("p1", "Back", position="RB", carries=200, targets=50),
        )

        assert table["SEA"][0].target_share == pytest.approx(0.10)

    def test_snap_share_is_stored_as_a_fraction(self):
        table = rushers(
            total("p1", "Back", position="RB", carries=200),
            snap_rows=[snap("p1", share=0.58, position="RB")],
        )

        assert table["SEA"][0].snap_share == pytest.approx(0.58)
