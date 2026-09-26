"""Defense vs. position.

The arithmetic is simple -- sum what a defense's opponents did, divide by its
games -- so the tests hold the edges where it could quietly be wrong: whose
games the denominator counts, which way a rank runs, what a fullback is, and
what "long" means when it is a per-game number rather than a season record.
"""

from __future__ import annotations

import polars as pl
import pytest

from pipeline.metrics.dvp import (
    DvpTables,
    allowed,
    allows_rows,
    player_games,
    player_lines,
    ranks,
    side,
)
from pipeline.sources.pbp import DVP_COLUMNS as PBP_DVP_COLUMNS
from pipeline.sources.pbp import PlayByPlayUnavailable
from pipeline.sources.players import DVP_COLUMNS as WEEKLY_DVP_COLUMNS

WEEKLY_SCHEMA = {
    "player_id": pl.Utf8,
    "player_display_name": pl.Utf8,
    "position": pl.Utf8,
    "game_id": pl.Utf8,
    "team": pl.Utf8,
    "opponent_team": pl.Utf8,
    "completions": pl.Int64,
    "attempts": pl.Int64,
    "passing_yards": pl.Float64,
    "passing_tds": pl.Int64,
    "passing_interceptions": pl.Int64,
    "receptions": pl.Int64,
    "targets": pl.Int64,
    "receiving_yards": pl.Float64,
    "receiving_tds": pl.Int64,
    "carries": pl.Int64,
    "rushing_yards": pl.Float64,
    "rushing_tds": pl.Int64,
    "fantasy_points_ppr": pl.Float64,
}

PLAY_SCHEMA = {
    "season_type": pl.Utf8,
    "game_id": pl.Utf8,
    "play_type": pl.Utf8,
    "qb_kneel": pl.Int64,
    "yardline_100": pl.Float64,
    "yards_gained": pl.Float64,
    "complete_pass": pl.Int64,
    "receiver_player_id": pl.Utf8,
    "rusher_player_id": pl.Utf8,
    "sack": pl.Int64,
    "qb_scramble": pl.Int64,
    "two_point_attempt": pl.Int64,
}


def line(player_id, *, game="g1", team="SEA", opponent="NE", position="WR", **over):
    """One player's box score in one game. Zeros unless stated."""
    row = {
        "player_id": player_id,
        "player_display_name": player_id.upper(),
        "position": position,
        "game_id": game,
        "team": team,
        "opponent_team": opponent,
        "completions": 0,
        "attempts": 0,
        "passing_yards": 0.0,
        "passing_tds": 0,
        "passing_interceptions": 0,
        "receptions": 0,
        "targets": 0,
        "receiving_yards": 0.0,
        "receiving_tds": 0,
        "carries": 0,
        "rushing_yards": 0.0,
        "rushing_tds": 0,
        "fantasy_points_ppr": 0.0,
    }
    row.update(over)
    return row


def catch(receiver, *, game="g1", gained=10, yardline=50, complete=1, **over):
    row = {
        "season_type": "REG",
        "game_id": game,
        "play_type": "pass",
        "qb_kneel": 0,
        "yardline_100": yardline,
        "yards_gained": gained,
        "complete_pass": complete,
        "receiver_player_id": receiver,
        "rusher_player_id": None,
        "sack": 0,
        "qb_scramble": 0,
        "two_point_attempt": 0,
    }
    row.update(over)
    return row


def run(rusher, *, game="g1", gained=4, yardline=50, **over):
    row = catch(None, game=game, gained=gained, yardline=yardline, complete=0)
    row.update(play_type="run", rusher_player_id=rusher)
    row.update(over)
    return row


def weekly(*rows):
    return pl.DataFrame(list(rows), schema=WEEKLY_SCHEMA)


def plays(*rows):
    return pl.DataFrame(list(rows), schema=PLAY_SCHEMA)


def games_of(weekly_rows, play_rows=()):
    return player_games(weekly(*weekly_rows), plays(*play_rows))


def allowed_of(weekly_rows, play_rows=()):
    return allowed(games_of(weekly_rows, play_rows))


def test_the_fixture_schemas_match_the_column_guards():
    """If the module starts reading a column, these fixtures must carry it."""
    assert set(WEEKLY_SCHEMA) == WEEKLY_DVP_COLUMNS
    assert set(PLAY_SCHEMA) == PBP_DVP_COLUMNS


class TestAllowed:
    def test_it_is_per_game_over_the_defenses_games(self):
        """Two games against SEA, 100 receiving yards to WRs in one of them.
        SEA allowed 50 a game -- the game with none still counts."""
        result = allowed_of(
            [
                line("wr1", game="g1", team="NE", opponent="SEA", receiving_yards=100.0),
                line("te1", game="g2", team="DAL", opponent="SEA", position="TE"),
            ]
        )

        assert result["SEA"]["WR"]["rec_yds"] == pytest.approx(50.0)

    def test_every_opponent_at_the_role_is_combined(self):
        result = allowed_of(
            [
                line("wr1", team="NE", opponent="SEA", receptions=5),
                line("wr2", team="NE", opponent="SEA", receptions=3),
            ]
        )

        assert result["SEA"]["WR"]["rec"] == pytest.approx(8.0)

    def test_a_fullback_counts_as_a_running_back(self):
        result = allowed_of(
            [
                line("rb1", team="NE", opponent="SEA", position="RB", carries=10),
                line("fb1", team="NE", opponent="SEA", position="FB", carries=2),
            ]
        )

        assert result["SEA"]["RB"]["carries"] == pytest.approx(12.0)

    def test_positions_outside_the_four_roles_are_ignored(self):
        """A punter's fake is real yardage, but no DvP row is about punters."""
        result = allowed_of(
            [
                line("wr1", team="NE", opponent="SEA", rushing_yards=5.0),
                line("p1", team="NE", opponent="SEA", position="P", rushing_yards=30.0),
            ]
        )

        assert result["SEA"]["WR"]["rush_yds"] == pytest.approx(5.0)
        assert "P" not in result["SEA"]

    def test_a_role_a_defense_never_faced_is_zero_not_missing(self):
        """Every defense is ranked on every role; not facing a TE is 0 allowed."""
        result = allowed_of([line("wr1", team="NE", opponent="SEA")])

        assert result["SEA"]["TE"]["rec"] == 0.0

    def test_arizona_is_one_team_whichever_way_the_feed_spells_it(self):
        result = allowed_of(
            [
                line("wr1", game="g1", team="NE", opponent="AZ", receptions=4),
                line("wr2", game="g2", team="DAL", opponent="ARI", receptions=6),
            ]
        )

        assert set(result) == {"ARI"}
        assert result["ARI"]["WR"]["rec"] == pytest.approx(5.0)

    def test_red_zone_looks_come_from_play_by_play(self):
        result = allowed_of(
            [line("wr1", team="NE", opponent="SEA")],
            [
                catch("wr1", yardline=20),  # in: the 20 is the red zone
                catch("wr1", yardline=21),  # out
                catch("wr1", yardline=10, complete=0),  # an incompletion is a target
                catch("wr1", yardline=10, sack=1),  # a sack is not
                catch("wr1", yardline=2, two_point_attempt=1),  # nor a two-point try
            ],
        )

        assert result["SEA"]["WR"]["rz_tgt"] == pytest.approx(2.0)

    def test_a_red_zone_carry_is_a_designed_run(self):
        """The same rule as the Inside 5 column: sneaks in, scrambles out."""
        result = allowed_of(
            [line("qb1", team="NE", opponent="SEA", position="QB")],
            [
                run("qb1", yardline=1),
                run("qb1", yardline=15, qb_scramble=1),
            ],
        )

        assert result["SEA"]["QB"]["rz_car"] == pytest.approx(1.0)

    def test_long_is_the_mean_of_each_games_longest(self):
        """Game 1's longest WR catch is 40, game 2's is 20: long rec is 30.
        A season maximum would say 40 and describe one play."""
        result = allowed_of(
            [
                line("wr1", game="g1", team="NE", opponent="SEA"),
                line("wr2", game="g1", team="NE", opponent="SEA"),
                line("wr3", game="g2", team="DAL", opponent="SEA"),
            ],
            [
                catch("wr1", game="g1", gained=40),
                catch("wr2", game="g1", gained=25),
                catch("wr3", game="g2", gained=20),
                catch("wr3", game="g2", gained=60, complete=0),  # not a catch
            ],
        )

        assert result["SEA"]["WR"]["long_rec"] == pytest.approx(30.0)

    def test_a_scramble_is_a_rush_for_long_rush(self):
        """The box score counts scrambles as carries; the long has to agree."""
        result = allowed_of(
            [line("qb1", team="NE", opponent="SEA", position="QB")],
            [run("qb1", gained=18, qb_scramble=1), run("qb1", gained=3)],
        )

        assert result["SEA"]["QB"]["long_rush"] == pytest.approx(18.0)

    def test_postseason_plays_are_left_out(self):
        result = allowed_of(
            [line("wr1", team="NE", opponent="SEA")],
            [catch("wr1", yardline=5, season_type="POST")],
        )

        assert result["SEA"]["WR"]["rz_tgt"] == 0.0


def three_defenses(**values):
    """WR receiving yards allowed by three defenses, one game each."""
    return allowed_of(
        [
            line(
                f"wr_{team}",
                game=f"g_{team}",
                team="NE",
                opponent=team,
                **{k: v[i] for k, v in values.items()},
            )
            for i, team in enumerate(("DAL", "NYG", "SEA"))
        ]
    )


class TestRanks:
    def test_rank_one_gives_up_the_most(self):
        result = ranks(three_defenses(receiving_yards=[80.0, 120.0, 100.0]))

        assert result["NYG"]["WR"]["rec_yds"] == 1
        assert result["SEA"]["WR"]["rec_yds"] == 2
        assert result["DAL"]["WR"]["rec_yds"] == 3

    def test_interceptions_are_inverted_so_one_still_favours_the_offense(self):
        result = ranks(
            allowed_of(
                [
                    line(
                        "qb_d",
                        game="g_d",
                        team="NE",
                        opponent="DAL",
                        position="QB",
                        passing_interceptions=2,
                    ),
                    line(
                        "qb_s",
                        game="g_s",
                        team="NE",
                        opponent="SEA",
                        position="QB",
                        passing_interceptions=0,
                    ),
                ]
            )
        )

        assert result["SEA"]["QB"]["int"] == 1
        assert result["DAL"]["QB"]["int"] == 2

    def test_ties_break_on_the_abbreviation_so_reruns_agree(self):
        result = ranks(three_defenses(receiving_yards=[100.0, 100.0, 100.0]))

        assert [result[t]["WR"]["rec_yds"] for t in ("DAL", "NYG", "SEA")] == [1, 2, 3]


class TestAllowsRows:
    def test_each_section_lists_its_roles_in_order_with_value_and_rank(self):
        tables = allows_rows(three_defenses(receiving_yards=[80.0, 120.0, 100.0]))

        receiving = tables["SEA"]["receiving"]
        assert [row.role for row in receiving] == ["WR", "TE", "RB"]
        cell = receiving[0].stats["rec_yds"]
        assert (cell.value, cell.rank) == (100.0, 2)

        assert [row.role for row in tables["SEA"]["passing"]] == ["QB"]
        assert [row.role for row in tables["SEA"]["rushing"]] == ["QB", "RB"]

    def test_ppr_is_the_roles_whole_total_in_every_section(self):
        """An RB's PPR allowed includes his catches and his carries, so the
        same number appears in Receiving and Rushing."""
        tables = allows_rows(
            allowed_of(
                [line("rb1", team="NE", opponent="SEA", position="RB", fantasy_points_ppr=21.5)]
            )
        )

        rb_receiving = tables["SEA"]["receiving"][2].stats["ppr"]
        rb_rushing = tables["SEA"]["rushing"][1].stats["ppr"]
        assert rb_receiving.value == rb_rushing.value == 21.5


def lines_of(weekly_rows, play_rows=(), current=None, **kwargs):
    games = games_of(weekly_rows, play_rows)
    ids = games["player_id"].unique().to_list()
    return player_lines(games, current or dict.fromkeys(ids, "NE"), **kwargs)


class TestPlayerLines:
    def test_a_line_is_per_game_over_his_own_games(self):
        result = lines_of(
            [
                line("wr1", game="g1", team="NE", targets=10, receiving_yards=100.0),
                line("wr1", game="g2", team="NE", targets=6, receiving_yards=40.0),
            ]
        )

        row = result["NE"]["receiving"][0]
        assert row.games == 2
        assert row.stats["rec_yds"] == pytest.approx(70.0)
        assert row.stats["tgt"] == pytest.approx(8.0)

    def test_a_player_not_on_a_roster_today_is_dropped(self):
        result = lines_of(
            [line("wr1", team="NE", targets=5), line("gone", team="NE", targets=9)],
            current={"wr1": "NE"},
        )

        assert [row.player for row in result["NE"]["receiving"]] == ["WR1"]

    def test_he_is_listed_under_his_current_team(self):
        """Week 1 reads last season. A receiver who moved in the offseason
        appears for the team he plays for on Sunday."""
        result = lines_of([line("wr1", team="NE", targets=5)], current={"wr1": "SEA"})

        assert "NE" not in result
        assert result["SEA"]["receiving"][0].player == "WR1"

    def test_receivers_are_ordered_by_targets_and_capped(self):
        result = lines_of(
            [line(f"wr{n}", team="NE", targets=n) for n in range(1, 6)],
            receiving_rows=3,
        )

        assert [row.player for row in result["NE"]["receiving"]] == ["WR5", "WR4", "WR3"]

    def test_a_fullback_is_ranked_against_the_rb_role(self):
        result = lines_of([line("fb1", team="NE", position="FB", targets=1)])

        row = result["NE"]["receiving"][0]
        assert (row.role, row.position) == ("RB", "FB")

    def test_the_quarterback_is_the_one_who_played_most(self):
        """A backup with one big afternoon is not the starter."""
        result = lines_of(
            [
                line("starter", game="g1", team="NE", position="QB", passing_yards=200.0),
                line("starter", game="g2", team="NE", position="QB", passing_yards=210.0),
                line("backup", game="g3", team="NE", position="QB", passing_yards=400.0),
            ]
        )

        assert [row.player for row in result["NE"]["passing"]] == ["STARTER"]

    def test_rushing_is_the_quarterback_then_backs_above_the_floor(self):
        result = lines_of(
            [
                line("qb1", team="NE", position="QB", carries=3),
                line("rb1", team="NE", position="RB", carries=15),
                line("rb2", team="NE", position="RB", carries=6),
                line("fb1", team="NE", position="FB", carries=0),
            ],
            min_carries=1.0,
        )

        assert [row.player for row in result["NE"]["rushing"]] == ["QB1", "RB1", "RB2"]
        assert result["NE"]["rushing"][0].role == "QB"

    def test_the_player_line_carries_the_same_long_rule(self):
        result = lines_of(
            [
                line("wr1", game="g1", team="NE", targets=3),
                line("wr1", game="g2", team="NE", targets=3),
            ],
            [
                catch("wr1", game="g1", gained=30),
                catch("wr1", game="g1", gained=12),
                # No catch at all in g2: his longest that day was nothing.
            ],
        )

        assert result["NE"]["receiving"][0].stats["long_rec"] == pytest.approx(15.0)


class TestSide:
    def test_an_offense_meets_the_other_teams_defense(self):
        allows = allows_rows(three_defenses(receiving_yards=[80.0, 120.0, 100.0]))
        players = lines_of([line("wr1", team="NE", targets=5)])
        tables = DvpTables(allows=allows, players=players)

        result = side(tables, offense="NE", defense="SEA")

        assert result.receiving.allows == allows["SEA"]["receiving"]
        assert result.receiving.players == players["NE"]["receiving"]

    def test_nothing_on_either_side_is_no_side(self):
        assert side(DvpTables(allows={}, players={}), offense="NE", defense="SEA") is None


class TestColumnGuard:
    def test_a_moved_column_fails_loudly_and_names_it(self):
        frame = weekly(line("wr1")).drop("opponent_team")

        with pytest.raises(PlayByPlayUnavailable, match="opponent_team"):
            player_games(frame, plays())
