"""Team efficiency: what counts as a play, and what counts as pace.

Every number here is one a reader could look up somewhere else, so the tests
are mostly about the filters rather than the arithmetic. Counting a kneel-down
as a run, or a TV timeout as tempo, produces a number that is wrong in a way
nobody notices until someone asks where it came from.
"""

from __future__ import annotations

import polars as pl
import pytest

from pipeline.metrics.efficiency import games_played, team_efficiency
from pipeline.sources.pbp import REQUIRED_COLUMNS

SCHEMA = {
    "season_type": pl.Utf8,
    "game_id": pl.Utf8,
    "play_id": pl.Int64,
    "posteam": pl.Utf8,
    "play_type": pl.Utf8,
    "qb_kneel": pl.Int64,
    "qb_spike": pl.Int64,
    "qb_dropback": pl.Int64,
    "pass_oe": pl.Float64,
    "epa": pl.Float64,
    "down": pl.Int64,
    "wp": pl.Float64,
    "fixed_drive": pl.Int64,
    "game_seconds_remaining": pl.Float64,
}


def play(**over):
    """One play-by-play row, neutral in every dimension the module filters on."""
    row = {
        "season_type": "REG",
        "game_id": "2025_01_NE_SEA",
        "play_id": 0,
        "posteam": "SEA",
        "play_type": "pass",
        "qb_kneel": 0,
        "qb_spike": 0,
        "qb_dropback": 1,
        "pass_oe": None,
        "epa": 0.0,
        "down": 1,
        "wp": 0.5,
        "fixed_drive": 1,
        "game_seconds_remaining": 3600.0,
    }
    row.update(over)
    return row


def frame(*plays):
    """A play-by-play frame with play_id running in the order given."""
    rows = [dict(p, play_id=i) for i, p in enumerate(plays)]
    return pl.DataFrame(rows, schema=SCHEMA)


class TestSchemaContract:
    def test_the_test_frame_carries_exactly_what_the_module_requires(self):
        """If this fails, REQUIRED_COLUMNS moved and these fixtures are stale."""
        assert set(SCHEMA) >= REQUIRED_COLUMNS


class TestWhichPlaysCount:
    def test_pass_and_rush_rate_are_complements_over_the_same_plays(self):
        table = team_efficiency(
            frame(play(play_type="pass"), play(play_type="pass"), play(play_type="run"))
        )

        assert table["SEA"].pass_rate == pytest.approx(2 / 3, abs=1e-4)
        assert table["SEA"].rush_rate == pytest.approx(1 / 3, abs=1e-4)

    def test_a_kneel_down_is_not_a_rushing_play(self):
        """Kneels are clock management. Counting them makes a team that
        protected a lead look more run-heavy than it played."""
        table = team_efficiency(
            frame(play(play_type="pass"), play(play_type="run", qb_kneel=1))
        )

        assert table["SEA"].pass_rate == pytest.approx(1.0)
        assert table["SEA"].plays_per_game == pytest.approx(1.0)

    def test_a_spike_is_not_a_passing_play(self):
        table = team_efficiency(
            frame(play(play_type="run"), play(play_type="pass", qb_spike=1))
        )

        assert table["SEA"].pass_rate == pytest.approx(0.0)
        assert table["SEA"].plays_per_game == pytest.approx(1.0)

    def test_special_teams_and_no_plays_are_excluded(self):
        table = team_efficiency(
            frame(play(play_type="pass"), play(play_type="punt"), play(play_type="no_play"))
        )

        assert table["SEA"].plays_per_game == pytest.approx(1.0)

    def test_the_postseason_does_not_count_toward_a_regular_season_number(self):
        table = team_efficiency(
            frame(play(epa=1.0), play(season_type="POST", epa=-5.0))
        )

        assert table["SEA"].epa_per_play == pytest.approx(1.0)

    def test_a_play_with_no_offense_is_dropped_rather_than_crashing(self):
        table = team_efficiency(frame(play(), play(posteam=None)))

        assert table["SEA"].plays_per_game == pytest.approx(1.0)


class TestPlaysPerGame:
    def test_it_divides_by_games_played_not_by_rows(self):
        table = team_efficiency(
            frame(
                play(game_id="g1"),
                play(game_id="g1"),
                play(game_id="g1"),
                play(game_id="g2"),
            )
        )

        assert table["SEA"].plays_per_game == pytest.approx(2.0)

    def test_games_played_counts_distinct_games(self):
        counts = games_played(
            frame(play(game_id="g1"), play(game_id="g1"), play(game_id="g2"))
        )

        assert counts == {"SEA": 2}


class TestProe:
    def test_it_is_stored_as_a_fraction_not_as_percentage_points(self):
        """nflfastR publishes pass_oe as +2.9; we store 0.029, like every other
        rate in the payload."""
        table = team_efficiency(frame(play(pass_oe=2.9), play(pass_oe=2.9)))

        assert table["SEA"].proe == pytest.approx(0.029, abs=1e-6)

    def test_plays_the_model_declined_to_score_are_excluded(self):
        """Substituting zero for 'no opinion' would drag every team toward
        neutral, which is a different claim from the one we are making."""
        table = team_efficiency(frame(play(pass_oe=6.0), play(pass_oe=None)))

        assert table["SEA"].proe == pytest.approx(0.06, abs=1e-6)

    def test_a_team_with_no_scored_plays_keeps_its_other_numbers(self):
        table = team_efficiency(frame(play(pass_oe=None, epa=0.5)))

        assert table["SEA"].proe is None
        assert table["SEA"].epa_per_play == pytest.approx(0.5)

    def test_proe_reads_plays_the_offensive_filter_would_have_dropped(self):
        """pass_oe is the model's opinion on a snap, not our play filter's.

        Penalties are the real case: 1,482 of 2025's `no_play` rows carry a
        pass_oe, and the pass-rate filter drops every one of them.
        """
        table = team_efficiency(
            frame(play(pass_oe=2.0), play(play_type="no_play", pass_oe=6.0))
        )

        # Both plays average into PROE; only the first is a play.
        assert table["SEA"].proe == pytest.approx(0.04, abs=1e-6)
        assert table["SEA"].plays_per_game == pytest.approx(1.0)


class TestPace:
    def snap(self, seconds, **over):
        return play(game_seconds_remaining=seconds, **over)

    def test_it_measures_the_gap_between_consecutive_snaps_of_a_drive(self):
        table = team_efficiency(frame(self.snap(3600.0), self.snap(3570.0)))

        assert table["SEA"].pace == pytest.approx(30.0)

    def test_the_first_play_of_a_drive_has_no_gap_to_measure(self):
        table = team_efficiency(frame(self.snap(3600.0)))

        assert table["SEA"].pace is None

    def test_a_gap_across_two_drives_is_not_pace(self):
        """The other team had the ball in between."""
        table = team_efficiency(
            frame(
                self.snap(3600.0, fixed_drive=1),
                self.snap(3000.0, fixed_drive=2),
                self.snap(2970.0, fixed_drive=2),
            )
        )

        assert table["SEA"].pace == pytest.approx(30.0)

    def test_a_stoppage_longer_than_a_minute_is_the_broadcast_not_the_huddle(self):
        """Timeouts, injuries, reviews, and TV breaks all land here."""
        table = team_efficiency(
            frame(self.snap(3600.0), self.snap(3570.0), self.snap(3400.0))
        )

        assert table["SEA"].pace == pytest.approx(30.0)

    def test_third_down_is_excluded(self):
        table = team_efficiency(
            frame(
                self.snap(3600.0, down=1),
                self.snap(3570.0, down=2),
                self.snap(3520.0, down=3),
            )
        )

        assert table["SEA"].pace == pytest.approx(30.0)

    def test_a_blowout_is_excluded_in_both_directions(self):
        """Trailing teams hurry and leading teams stall, which says more about
        the scoreboard than about the offense."""
        table = team_efficiency(
            frame(
                self.snap(3600.0, wp=0.5),
                self.snap(3570.0, wp=0.5),
                self.snap(3520.0, wp=0.95),
                self.snap(3470.0, wp=0.05),
            )
        )

        assert table["SEA"].pace == pytest.approx(30.0)


class TestEpaRank:
    def test_the_best_offense_ranks_first(self):
        table = team_efficiency(
            frame(
                play(posteam="SEA", epa=0.3),
                play(posteam="NE", epa=0.1),
                play(posteam="KC", epa=-0.2),
            )
        )

        assert (table["SEA"].epa_rank, table["NE"].epa_rank, table["KC"].epa_rank) == (
            1,
            2,
            3,
        )

    def test_a_tie_is_broken_the_same_way_on_every_run(self):
        """Two teams swapping ranks between hourly builds would look like news."""
        built = frame(play(posteam="SEA", epa=0.2), play(posteam="NE", epa=0.2))
        first, second = team_efficiency(built), team_efficiency(built)

        assert first["NE"].epa_rank == second["NE"].epa_rank == 1
        assert first["SEA"].epa_rank == second["SEA"].epa_rank == 2


class TestEmptyInput:
    def test_a_frame_with_no_offensive_plays_yields_an_empty_table(self):
        """Empty, not an exception: the rest of the page is still publishable."""
        assert team_efficiency(frame(play(play_type="punt"))) == {}
