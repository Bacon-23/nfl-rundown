"""Attaching the stat modules to a week, and noticing when one is empty.

The interesting behaviour here is not the arithmetic -- that is tested in
test_efficiency, test_passing, and test_rushing -- but the failure reporting. A
team whose players quietly fail to join publishes two blank tables and says
nothing about why, which is how Arizona's "AZ" spelling went unnoticed until it
was looked for directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pipeline.build_week import _games_sampled, _missing_team_warnings
from pipeline.schema import Game, Kickoff, Odds, ReceiverRow, Team, TeamEfficiency


def game(away="NE", home="SEA"):
    return Game(
        game_id=f"2026_01_{away}_{home}",
        season=2026,
        week=1,
        away=Team(abbr=away, name=away),
        home=Team(abbr=home, name=home),
        kickoff=Kickoff(utc=datetime(2026, 9, 10, 0, 20, tzinfo=UTC), display="Wed"),
        odds=Odds(source="odds_api"),
    )


def receiver(name="A Receiver"):
    return ReceiverRow(player=name, target_share=0.2)


class TestMissingTeamWarnings:
    def test_a_full_table_produces_no_warning(self):
        built = [game()]
        table = {"NE": [receiver()], "SEA": [receiver()]}

        assert _missing_team_warnings(built, {}, table, {}) == []

    def test_a_team_with_an_empty_table_is_named(self):
        built = [game()]
        table = {"NE": [receiver()]}

        warnings = _missing_team_warnings(built, {}, table, {})

        assert len(warnings) == 1
        assert "SEA" in warnings[0]
        assert "receivers" in warnings[0]

    def test_a_feed_that_produced_nothing_at_all_is_left_to_its_own_handler(self):
        """The module's own try/except already reported it. Naming all 32 teams
        here would bury that message rather than add to it."""
        assert _missing_team_warnings([game()], {}, {}, {}) == []

    def test_each_feed_is_reported_separately(self):
        built = [game()]

        warnings = _missing_team_warnings(
            built,
            {"NE": TeamEfficiency(team="NE")},
            {"NE": [receiver()]},
            {"NE": [receiver()]},
        )

        assert len(warnings) == 3
        assert all("SEA" in w for w in warnings)

    def test_every_team_in_the_week_is_checked_not_just_the_first_game(self):
        built = [game("NE", "SEA"), game("DAL", "NYG")]
        table = {"NE": [receiver()], "SEA": [receiver()], "DAL": [receiver()]}

        warnings = _missing_team_warnings(built, {}, table, {})

        assert "NYG" in warnings[0]


class TestGamesSampled:
    def test_it_takes_the_thinner_half_of_the_table(self):
        """After a bye the two sides differ by a game. The badge is a claim
        about the whole table, so it has to describe the weaker half."""
        assert _games_sampled({"NE": 4, "SEA": 3}, ("NE", "SEA")) == 3

    def test_one_known_side_is_better_than_none(self):
        assert _games_sampled({"NE": 4}, ("NE", "SEA")) == 4

    def test_neither_side_known_yields_no_count(self):
        """The badge then says "early season" rather than inventing a number."""
        assert _games_sampled({}, ("NE", "SEA")) is None
