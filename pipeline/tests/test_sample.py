"""Which sample a stat table describes, and the badge that says so.

The badge is the difference between publishing a three-game sample honestly and
publishing it as though it were settled. These tests pin the cutovers, because
the constants behind them are meant to be moved and moving one should not
silently change what the reader is told.
"""

from __future__ import annotations

import pytest

from pipeline import config
from pipeline.metrics.sample import badge_for, basis_for, describe

SEASON = 2026


class TestBasis:
    def test_week_one_reads_the_prior_season(self):
        assert basis_for(SEASON, 1) == "prior_season"

    @pytest.mark.parametrize("week", [2, 3, 4])
    def test_the_early_weeks_are_a_small_sample(self, week):
        assert basis_for(SEASON, week) == "small_sample"

    @pytest.mark.parametrize("week", [5, 10, 18])
    def test_from_week_five_the_season_speaks_for_itself(self, week):
        assert basis_for(SEASON, week) == "current_season"

    def test_the_cutovers_come_from_config_rather_than_from_here(self):
        """Both boundaries are single constants, by design."""
        assert basis_for(SEASON, config.PRIOR_SEASON_THROUGH_WEEK) == "prior_season"
        assert basis_for(SEASON, config.SMALL_SAMPLE_THROUGH_WEEK) == "small_sample"
        assert basis_for(SEASON, config.SMALL_SAMPLE_THROUGH_WEEK + 1) == "current_season"


class TestBadge:
    def test_week_one_is_badged_with_the_season_it_actually_read(self):
        """2026 week 1 shows 2025 numbers, and says so."""
        assert badge_for(2026, 1) == "2025 season"

    def test_a_small_sample_states_its_size(self):
        assert badge_for(SEASON, 3, games_sampled=2) == "n = 2 games"

    def test_one_game_is_not_pluralised(self):
        assert badge_for(SEASON, 2, games_sampled=1) == "n = 1 game"

    def test_an_unknown_small_sample_size_is_not_invented(self):
        """A module that could not count its games says so rather than guessing."""
        assert badge_for(SEASON, 3, games_sampled=None) == "early season"

    def test_a_settled_season_carries_no_badge(self):
        assert badge_for(SEASON, 9, games_sampled=8) is None

    def test_the_game_count_is_ignored_once_the_season_is_settled(self):
        assert badge_for(SEASON, 12, games_sampled=3) is None


class TestDescribe:
    def test_it_returns_the_pair_every_caller_wants(self):
        assert describe(2026, 1, 17) == ("prior_season", "2025 season")
        assert describe(2026, 3, 2) == ("small_sample", "n = 2 games")
        assert describe(2026, 8, 7) == ("current_season", None)
