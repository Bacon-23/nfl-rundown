"""ATS and over/under season records.

The arithmetic is easy to get subtly wrong in ways nobody notices until a
published record disagrees with every other site: an exact push counted as a
cover, the away side of a spread inverted, or a playoff run inflating what is
labelled a regular-season record. Each of those has its own test.
"""

from __future__ import annotations

import dataclasses

import pytest

from pipeline.metrics.records import Record, season_records
from pipeline.tests.test_odds import make_game


def played(
    away="NE",
    home="SEA",
    *,
    away_score,
    home_score,
    spread_line=3.5,
    total_line=44.5,
    week=1,
):
    """A completed game. `spread_line` is positive when the home side is favored."""
    return dataclasses.replace(
        make_game(away=away, home=home, spread_line=spread_line, total_line=total_line),
        game_id=f"2025_{week:02d}_{away}_{home}",
        season=2025,
        week=week,
        away_score=away_score,
        home_score=home_score,
    )


class TestAgainstTheSpread:
    def test_home_favorite_that_wins_by_more_than_the_number_covers(self):
        # SEA favored by 3.5, wins by 10.
        games = [played(away_score=17, home_score=27, spread_line=3.5)]
        records = season_records(games)

        assert records["SEA"].ats == Record(won=1, lost=0, pushed=0)
        assert records["NE"].ats == Record(won=0, lost=1, pushed=0)

    def test_home_favorite_that_wins_by_less_than_the_number_does_not_cover(self):
        # SEA favored by 3.5, wins by 3. The away side covers.
        games = [played(away_score=24, home_score=27, spread_line=3.5)]
        records = season_records(games)

        assert records["SEA"].ats == Record(won=0, lost=1, pushed=0)
        assert records["NE"].ats == Record(won=1, lost=0, pushed=0)

    def test_home_underdog_covers_by_losing_narrowly(self):
        # SEA are 3.5-point dogs and lose by 3.
        games = [played(away_score=27, home_score=24, spread_line=-3.5)]
        records = season_records(games)

        assert records["SEA"].ats == Record(won=1, lost=0, pushed=0)
        assert records["NE"].ats == Record(won=0, lost=1, pushed=0)

    def test_landing_exactly_on_the_number_is_a_push_for_both_sides(self):
        """A push is not half a win and not a loss. It is its own column."""
        games = [played(away_score=21, home_score=24, spread_line=3.0)]
        records = season_records(games)

        assert records["SEA"].ats == Record(won=0, lost=0, pushed=1)
        assert records["NE"].ats == Record(won=0, lost=0, pushed=1)

    def test_a_pick_em_is_decided_by_the_winner(self):
        games = [played(away_score=20, home_score=23, spread_line=0.0)]
        records = season_records(games)

        assert records["SEA"].ats == Record(won=1, lost=0, pushed=0)
        assert records["NE"].ats == Record(won=0, lost=1, pushed=0)


class TestOverUnder:
    def test_combined_points_above_the_total_is_an_over_for_both_teams(self):
        games = [played(away_score=27, home_score=24, total_line=44.5)]
        records = season_records(games)

        assert records["SEA"].ou == Record(won=1, lost=0, pushed=0)
        assert records["NE"].ou == Record(won=1, lost=0, pushed=0)

    def test_combined_points_below_the_total_is_an_under(self):
        games = [played(away_score=10, home_score=13, total_line=44.5)]
        records = season_records(games)

        assert records["SEA"].ou == Record(won=0, lost=1, pushed=0)

    def test_landing_exactly_on_the_total_is_a_push(self):
        games = [played(away_score=21, home_score=23, total_line=44.0)]
        records = season_records(games)

        assert records["SEA"].ou == Record(won=0, lost=0, pushed=1)


class TestWhichGamesCount:
    def test_a_game_that_has_not_been_played_is_ignored(self):
        unplayed = dataclasses.replace(
            played(away_score=0, home_score=0), away_score=None, home_score=None
        )
        assert season_records([unplayed]) == {}

    def test_a_game_with_no_posted_spread_still_counts_toward_the_ou_record(self):
        """Missing one line should not discard the other."""
        games = [played(away_score=27, home_score=24, spread_line=None)]
        records = season_records(games)

        assert records["SEA"].ats == Record()
        assert records["SEA"].ou == Record(won=1, lost=0, pushed=0)

    def test_a_game_with_no_posted_total_still_counts_toward_the_ats_record(self):
        games = [played(away_score=17, home_score=27, total_line=None)]
        records = season_records(games)

        assert records["SEA"].ats == Record(won=1, lost=0, pushed=0)
        assert records["SEA"].ou == Record()

    def test_playoff_games_do_not_inflate_a_regular_season_record(self):
        """Week 19+ is the postseason. The badge says '2025 season'; it means the
        regular season, which is what every other site publishes."""
        games = [
            played(away_score=17, home_score=27, week=18),
            played(away_score=17, home_score=27, week=19),
        ]
        records = season_records(games)

        assert records["SEA"].ats == Record(won=1, lost=0, pushed=0)


class TestAccumulation:
    def test_a_team_accrues_across_home_and_away_games(self):
        games = [
            # SEA at home, favored by 3.5, wins by 10 -> cover.
            played(away="NE", home="SEA", away_score=17, home_score=27, week=1),
            # SEA on the road, favored by 7 (home line -7), wins by 3 -> no cover.
            played(away="SEA", home="ARI", away_score=24, home_score=21,
                   spread_line=-7.0, week=2),
            # SEA on the road, exactly on the number -> push.
            played(away="SEA", home="ARI", away_score=24, home_score=21,
                   spread_line=-3.0, week=3),
        ]
        records = season_records(games)

        assert records["SEA"].ats == Record(won=1, lost=1, pushed=1)

    def test_a_team_with_no_completed_games_is_absent_rather_than_zero_zero(self):
        """'0-0' reads as a real record. Absent lets the renderer show a dash."""
        assert "KC" not in season_records([played(away_score=17, home_score=27)])


class TestFormatting:
    def test_a_record_without_pushes_omits_the_third_number(self):
        assert str(Record(won=10, lost=6, pushed=0)) == "10-6"

    def test_a_record_with_pushes_shows_all_three(self):
        assert str(Record(won=9, lost=7, pushed=1)) == "9-7-1"

    def test_an_empty_record_renders_as_nothing_rather_than_zero_zero(self):
        assert str(Record()) == ""

    @pytest.mark.parametrize(
        "record,expected",
        [(Record(1, 0, 0), "1-0"), (Record(0, 0, 2), "0-0-2")],
    )
    def test_edge_formats(self, record, expected):
        assert str(record) == expected
