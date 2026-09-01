"""Kickoff conversion and formatting.

nflverse stores kickoff as a local date plus an Eastern wall-clock time. Late
Sunday and Monday-night games cross midnight UTC, and the season straddles the
November DST change, so these conversions are worth pinning down rather than
trusting by eye.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pipeline.sources.schedule import _kickoff_utc, _nth_weekday, format_kickoff


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


class TestKickoffToUtc:
    def test_sunday_afternoon_in_daylight_time(self):
        # 1:00pm EDT is 17:00 UTC.
        assert _kickoff_utc("2026-09-13", "13:00") == utc(2026, 9, 13, 17, 0)

    def test_night_game_rolls_into_the_next_utc_day(self):
        # 8:20pm EDT Wednesday is 00:20 UTC Thursday.
        assert _kickoff_utc("2026-09-09", "20:20") == utc(2026, 9, 10, 0, 20)

    def test_after_the_november_change_the_offset_shifts(self):
        # DST ends Sunday Nov 1, 2026, so 1:00pm EST is 18:00 UTC.
        assert _kickoff_utc("2026-11-08", "13:00") == utc(2026, 11, 8, 18, 0)

    def test_the_week_before_the_change_is_still_daylight_time(self):
        assert _kickoff_utc("2026-10-25", "13:00") == utc(2026, 10, 25, 17, 0)

    def test_january_playoff_game_is_standard_time(self):
        assert _kickoff_utc("2027-01-10", "16:30") == utc(2027, 1, 10, 21, 30)

    def test_missing_time_defaults_to_one_oclock_not_midnight(self):
        """An unslotted game should sort into its own day, not the day before."""
        assert _kickoff_utc("2026-09-13", None) == utc(2026, 9, 13, 17, 0)


class TestDstBoundaries:
    def test_second_sunday_in_march_2026(self):
        assert _nth_weekday(2026, 3, weekday=6, n=2) == 8

    def test_first_sunday_in_november_2026(self):
        assert _nth_weekday(2026, 11, weekday=6, n=1) == 1

    @pytest.mark.parametrize(
        "year,expected",
        [(2024, 3), (2025, 2), (2026, 1), (2027, 7)],
    )
    def test_first_november_sunday_across_years(self, year, expected):
        assert _nth_weekday(year, 11, weekday=6, n=1) == expected


class TestFormatKickoff:
    def test_reads_as_eastern_not_utc(self):
        assert format_kickoff(utc(2026, 9, 10, 0, 20)) == "Wed · 8:20pm ET"

    def test_drops_the_minutes_on_the_hour(self):
        assert format_kickoff(utc(2026, 9, 13, 17, 0)) == "Sun · 1pm ET"

    def test_late_afternoon_window(self):
        assert format_kickoff(utc(2026, 9, 13, 20, 25)) == "Sun · 4:25pm ET"

    def test_noon_is_pm_not_am(self):
        assert format_kickoff(utc(2026, 9, 13, 16, 0)) == "Sun · 12pm ET"
