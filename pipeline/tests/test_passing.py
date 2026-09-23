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

from pipeline.metrics.passing import WeeklyTargets, receivers, target_rate
from pipeline.schema import ReceiverRow

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


# ---------------------------------------------------------------------------
# Share of team targets, week by week.
# ---------------------------------------------------------------------------

WEEK_SCHEMA = {
    "player_id": pl.Utf8,
    "team": pl.Utf8,
    "week": pl.Int64,
    "targets": pl.Int64,
}

APPEARANCE_SCHEMA = {"player_id": pl.Utf8, "team": pl.Utf8, "week": pl.Int64}


def week(player_id, team, wk, targets):
    return {"player_id": player_id, "team": team, "week": wk, "targets": targets}


def team_week(team, wk, targets):
    """The rest of a team's targets that week, credited to somebody else."""
    return week(f"rest-{team}-{wk}", team, wk, targets)


def weekly(stat_rows, appearances=()):
    """Box-score rows, plus snap appearances that have no box-score row."""
    return WeeklyTargets.from_frames(
        pl.DataFrame(list(stat_rows), schema=WEEK_SCHEMA),
        pl.DataFrame(
            [{"player_id": p, "team": t, "week": w} for p, t, w in appearances],
            schema=APPEARANCE_SCHEMA,
        ),
    )


class TestRecentWeeks:
    def test_it_is_the_last_four_weeks_the_team_played_oldest_first(self):
        built = weekly([team_week("SEA", wk, 30) for wk in range(1, 7)])

        assert built.recent_weeks("SEA") == [3, 4, 5, 6]

    def test_a_bye_is_skipped_rather_than_shown_as_an_empty_column(self):
        """The columns are games. A bye in week 5 means the fourth-last game
        was week 3, not an empty week-5 column."""
        built = weekly([team_week("SEA", wk, 30) for wk in (1, 2, 3, 4, 6, 7)])

        assert built.recent_weeks("SEA") == [3, 4, 6, 7]

    def test_early_in_the_season_there_are_fewer_columns(self):
        built = weekly([team_week("SEA", 1, 30), team_week("SEA", 2, 30)])

        assert built.recent_weeks("SEA") == [1, 2]

    def test_a_team_with_no_games_gets_no_columns(self):
        assert weekly([team_week("SEA", 1, 30)]).recent_weeks("NE") == []


class TestWeeklyShares:
    def test_a_cell_is_his_targets_over_his_teams_targets_that_week(self):
        built = weekly([week("p1", "SEA", 1, 6), team_week("SEA", 1, 24)])

        cells, _ = built.shares("p1", [1])

        assert cells == [pytest.approx(0.20)]

    def test_a_week_he_did_not_play_is_none_not_zero(self):
        built = weekly(
            [week("p1", "SEA", 1, 6), team_week("SEA", 1, 24), team_week("SEA", 2, 30)]
        )

        cells, _ = built.shares("p1", [1, 2])

        assert cells[1] is None

    def test_a_snap_without_a_box_score_row_is_zero_not_none(self):
        """nflverse writes no row for a tight end who blocked all afternoon.
        He played, and his share was nothing: that is 0%, not a dash."""
        built = weekly([team_week("SEA", 1, 30)], appearances=[("p1", "SEA", 1)])

        cells, l4 = built.shares("p1", [1])

        assert cells == [0.0]
        assert l4 == 0.0

    def test_a_traded_player_is_measured_against_the_team_he_played_for(self):
        built = weekly(
            [
                week("p1", "NYJ", 1, 10),
                team_week("NYJ", 1, 30),
                week("p1", "SEA", 2, 5),
                team_week("SEA", 2, 15),
            ]
        )

        cells, _ = built.shares("p1", [1, 2])

        assert cells == [pytest.approx(0.25), pytest.approx(0.25)]

    def test_l4_sums_targets_rather_than_averaging_the_weekly_shares(self):
        """10 of 20 and 2 of 40 is 12 of 60 -- 20% -- not the 27.5% that an
        average of 50% and 5% would claim."""
        built = weekly(
            [
                week("p1", "SEA", 1, 10),
                team_week("SEA", 1, 10),
                week("p1", "SEA", 2, 2),
                team_week("SEA", 2, 38),
            ]
        )

        _, l4 = built.shares("p1", [1, 2])

        assert l4 == pytest.approx(0.20)

    def test_l4_leaves_out_the_weeks_he_missed(self):
        """A receiver back from injury is judged on the games he played."""
        built = weekly(
            [team_week("SEA", 1, 40), week("p1", "SEA", 2, 10), team_week("SEA", 2, 30)]
        )

        _, l4 = built.shares("p1", [1, 2])

        assert l4 == pytest.approx(0.25)

    def test_a_player_who_played_none_of_the_weeks_has_no_l4(self):
        cells, l4 = weekly([team_week("SEA", 1, 40)]).shares("p1", [1])

        assert cells == [None]
        assert l4 is None

    def test_cells_line_up_with_the_weeks_asked_for(self):
        built = weekly([week("p1", "SEA", wk, wk) for wk in range(1, 5)])

        cells, _ = built.shares("p1", [2, 4])

        assert cells == [pytest.approx(1.0), pytest.approx(1.0)]


class TestReceiversCarryWeeklyShares:
    def test_week_1_reads_last_seasons_weeks_for_the_team_he_is_on_now(self):
        """The columns are his new team's last games. His cells are what he did
        in those weeks, wherever he did it -- he was playing."""
        built = weekly(
            [
                week("p1", "SEA", 17, 5),
                team_week("SEA", 17, 20),
                week("p1", "SEA", 18, 10),
                team_week("SEA", 18, 30),
                team_week("NE", 17, 30),
                team_week("NE", 18, 30),
            ]
        )

        table = receivers(
            totals(total("p1", "Moved On", targets=100, production_team="SEA")),
            team_targets(SEA=500),
            snaps(snap("p1")),
            {"SEA": 600},
            {"p1": "NE"},
            weekly=built,
        )

        row = table["NE"][0]
        assert row.weekly_share == [pytest.approx(0.20), pytest.approx(0.25)]
        assert row.l4_share == pytest.approx(15 / 65, abs=1e-4)

    def test_without_weekly_data_the_season_table_is_unchanged(self):
        table = receivers(
            totals(total("p1", "One", targets=100)),
            team_targets(SEA=500),
            snaps(snap("p1")),
            {"SEA": 600},
            {"p1": "SEA"},
        )

        row = table["SEA"][0]
        assert row.target_share == pytest.approx(0.20)
        assert row.weekly_share == []
        assert row.l4_share is None

    def test_a_weekly_failure_costs_the_weekly_cells_and_nothing_else(self):
        """Production's cron runs this code before production's plugin can
        show it, so a bug in the new columns must not blank the old table."""

        class Broken:
            def recent_weeks(self, team):
                raise RuntimeError("boom")

            def shares(self, player_id, weeks):
                raise RuntimeError("boom")

        table = receivers(
            totals(total("p1", "One", targets=100)),
            team_targets(SEA=500),
            snaps(snap("p1")),
            {"SEA": 600},
            {"p1": "SEA"},
            weekly=Broken(),
        )

        row = table["SEA"][0]
        assert row.target_share == pytest.approx(0.20)
        assert row.weekly_share == []
        assert row.l4_share is None


class TestPayloadContract:
    def test_the_fields_the_live_plugin_reads_are_still_there(self):
        """Production keeps its current plugin until staging is signed off,
        and that plugin reads these keys by name."""
        assert {
            "player",
            "role",
            "target_share",
            "target_rate",
            "rec_yds_per_game",
        } <= set(ReceiverRow.model_fields)
