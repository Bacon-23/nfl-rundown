"""Wiring season records onto a built week.

Two rules are easy to break silently: Week 1 must show *last* season's records,
because the current season has no completed games; and a team the tally never
saw must render as a dash, not as "0-0".
"""

from __future__ import annotations

from datetime import UTC, datetime

from pipeline import config
from pipeline.build_week import _attach_records
from pipeline.metrics.records import Record, TeamRecords
from pipeline.schema import Game, Kickoff, Odds, Team


def make_built(away="NE", home="SEA") -> Game:
    return Game(
        game_id=f"2026_01_{away}_{home}",
        season=2026,
        week=1,
        away=Team(abbr=away, name=away),
        home=Team(abbr=home, name=home),
        kickoff=Kickoff(utc=datetime(2026, 9, 10, 0, 20, tzinfo=UTC), display="Wed · 8:20pm ET"),
        odds=Odds(source="nflverse_fallback"),
    )

class TestWhichSeasonSuppliesTheRecord:
    def test_week_one_uses_the_prior_season(self):
        """There are no completed 2026 games when Week 1 is being written."""
        assert config.stats_season(2026, 1) == 2025

    def test_week_two_uses_the_current_season(self):
        assert config.stats_season(2026, 2) == 2026

    def test_late_season_uses_the_current_season(self):
        assert config.stats_season(2026, 12) == 2026

class TestAttachRecords:
    def test_both_sides_get_their_own_records(self):
        game = make_built()
        _attach_records(
            [game],
            {
                "SEA": TeamRecords(ats=Record(10, 6, 1), ou=Record(9, 8, 0)),
                "NE": TeamRecords(ats=Record(7, 10, 0), ou=Record(8, 9, 0)),
            },
        )

        assert game.home.ats_record == "10-6-1"
        assert game.home.ou_record == "9-8"
        assert game.away.ats_record == "7-10"

    def test_a_team_the_tally_never_saw_stays_none(self):
        game = make_built()
        _attach_records([game], {})

        assert game.home.ats_record is None
        assert game.away.ats_record is None

    def test_an_empty_record_stays_none_rather_than_an_empty_string(self):
        """An expansion-week team with no games should render a dash."""
        game = make_built()
        _attach_records([game], {"SEA": TeamRecords()})

        assert game.home.ats_record is None
        assert game.home.ou_record is None
