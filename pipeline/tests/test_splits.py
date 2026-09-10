"""The home and away split tables.

Two things here need defending. The first is the window: seventeen games taken
per player, newest first, across a season boundary if that is where they are.
The second is the floor, which is what keeps a two-game home sample from being
published as a home average -- the whole failure mode this module invites.

The kicker tests exist mostly to hold one line: no points column. nflverse
scores every kicker zero, so any points here would be a scoring rule we made
up, and a test is the cheapest place to say so out loud.
"""

from __future__ import annotations

import polars as pl
import pytest

from pipeline import config
from pipeline.metrics.splits import (
    fantasy_splits,
    kicker_splits,
    trailing,
    with_venue,
)

WEEKLY_SCHEMA = {
    "player_id": pl.Utf8,
    "player_display_name": pl.Utf8,
    "position": pl.Utf8,
    "season": pl.Int64,
    "week": pl.Int64,
    "team": pl.Utf8,
    "game_id": pl.Utf8,
    "fantasy_points_ppr": pl.Float64,
    "fg_made": pl.Int64,
    "fg_att": pl.Int64,
    "fg_long": pl.Int64,
}


def week(player_id, season=2025, week=1, *, home=True, opponent="ARI", **over):
    """One player-week, with a game_id whose home side matches `home`."""
    team = over.get("team", "SEA")
    away, at_home = (opponent, team) if home else (team, opponent)

    row = {
        "player_id": player_id,
        "player_display_name": player_id.upper(),
        "position": "WR",
        "season": season,
        "week": week,
        "team": team,
        "game_id": f"{season}_{week:02d}_{away}_{at_home}",
        "fantasy_points_ppr": 10.0,
        "fg_made": 0,
        "fg_att": 0,
        "fg_long": None,
    }
    row.update(over)
    return row


def weeks(*rows):
    return pl.DataFrame(list(rows), schema=WEEKLY_SCHEMA)


def schedule(frame):
    """The home team of every game the frame mentions, read off the game_id.

    The module takes this mapping from the schedule feed; here it is derived
    from the fixtures so a test can state a venue in one place.
    """
    return {
        game_id: game_id.split("_")[3] for game_id in frame["game_id"].unique().to_list()
    }


def venued(*rows):
    frame = weeks(*rows)
    return with_venue(frame, schedule(frame))


def splits(*rows, current=None, **kwargs):
    """Run the fantasy table over one team's worth of hand-built weeks."""
    frame = venued(*rows)
    ids = frame["player_id"].unique().to_list()
    return fantasy_splits(
        trailing(frame), current or dict.fromkeys(ids, "SEA"), **kwargs
    )


def kickers(*rows, current=None, **kwargs):
    frame = venued(*rows)
    ids = frame["player_id"].unique().to_list()
    return kicker_splits(
        trailing(frame), current or dict.fromkeys(ids, "SEA"), **kwargs
    )


def season_of(player_id, season, count, *, home=True, points=10.0, **over):
    """`count` consecutive weeks of one season for one player."""
    return [
        week(
            player_id,
            season=season,
            week=n,
            home=home,
            fantasy_points_ppr=points,
            **over,
        )
        for n in range(1, count + 1)
    ]


class TestVenue:
    def test_the_home_side_of_the_game_id_is_the_home_team(self):
        frame = venued(week("p1", home=True), week("p1", week=2, home=False))

        assert frame.sort("week")["is_home"].to_list() == [True, False]

    def test_arizona_joins_despite_the_roster_feed_calling_it_az(self):
        """nflverse codes Arizona "AZ" in one feed and "ARI" in every other.
        Unmapped, this would not raise -- it would mark every Arizona home game
        an away game, which looks like data rather than like a bug."""
        frame = venued(week("p1", team="AZ", opponent="SEA", home=True))

        assert frame["is_home"].to_list() == [True]

    def test_a_game_the_schedule_does_not_know_is_dropped(self):
        frame = weeks(week("p1"), week("p1", week=2))
        only_first = {frame["game_id"][0]: "SEA"}

        assert with_venue(frame, only_first).height == 1


class TestWindow:
    def test_it_keeps_the_most_recent_games_not_the_first(self):
        rows = [
            week("p1", week=n, fantasy_points_ppr=float(n))
            for n in range(1, 21)
        ]
        kept = trailing(venued(*rows), games=3)

        assert sorted(kept["week"].to_list()) == [18, 19, 20]

    def test_it_crosses_the_season_boundary_to_fill_the_window(self):
        """A window measured in games has to reach back into last season, or
        it is a season window wearing a different name."""
        rows = [
            *season_of("p1", 2024, 17),
            *season_of("p1", 2025, 2),
        ]
        kept = trailing(venued(*rows), games=17)

        assert kept.filter(pl.col("season") == 2025).height == 2
        assert kept.filter(pl.col("season") == 2024).height == 15

    def test_the_window_is_counted_per_player_not_per_team(self):
        """A back who missed six weeks is judged on the last seventeen games he
        played, not the seventeen his team played without him."""
        rows = [
            *season_of("starter", 2025, 6),
            *season_of("returning", 2025, 2),
        ]
        kept = trailing(venued(*rows), games=4)

        counts = dict(
            kept.group_by("player_id").len().iter_rows()
        )
        assert counts == {"starter": 4, "returning": 2}

    def test_the_window_length_comes_from_config_rather_than_from_here(self):
        rows = [week("p1", week=n) for n in range(1, 25)]

        assert trailing(venued(*rows)).height == config.SPLIT_TRAILING_GAMES


class TestFantasySplits:
    def test_it_averages_each_venue_separately(self):
        table = splits(
            *season_of("p1", 2025, 4, home=True, points=20.0),
            *[
                week("p1", week=n, home=False, fantasy_points_ppr=10.0)
                for n in range(5, 9)
            ],
        )
        row = table["SEA"][0]

        assert row.ppr_home == pytest.approx(20.0)
        assert row.ppr_away == pytest.approx(10.0)
        assert row.ppr_per_game == pytest.approx(15.0)

    def test_the_split_is_home_minus_away(self):
        table = splits(
            *season_of("p1", 2025, 3, home=True, points=18.0),
            *[
                week("p1", week=n, home=False, fantasy_points_ppr=12.0)
                for n in range(4, 7)
            ],
        )

        assert table["SEA"][0].ppr_split == pytest.approx(6.0)

    def test_a_thin_venue_is_a_dash_and_the_player_keeps_his_row(self):
        """Two home games is not a home average. His overall PPR is still a
        real number, so dropping him entirely would lose more than it saves."""
        table = splits(
            *season_of("p1", 2025, 2, home=True, points=30.0),
            *[
                week("p1", week=n, home=False, fantasy_points_ppr=10.0)
                for n in range(3, 9)
            ],
            min_side=3,
        )
        row = table["SEA"][0]

        assert row.ppr_home is None
        assert row.home_games == 2
        assert row.ppr_away == pytest.approx(10.0)
        assert row.ppr_per_game is not None

    def test_a_split_against_a_dash_is_also_a_dash(self):
        table = splits(
            *season_of("p1", 2025, 2, home=True),
            *[week("p1", week=n, home=False) for n in range(3, 9)],
            min_side=3,
        )

        assert table["SEA"][0].ppr_split is None

    def test_the_game_counts_travel_with_the_row(self):
        """The module badge quotes the window, not this player. Without the
        counts, nine games and seventeen would read identically."""
        table = splits(
            *season_of("p1", 2025, 3, home=True),
            *[week("p1", week=n, home=False) for n in range(4, 9)],
        )
        row = table["SEA"][0]

        assert (row.home_games, row.away_games) == (3, 5)


class TestWhoAppears:
    def test_the_quarterback_leads_even_when_a_receiver_outscores_him(self):
        table = splits(
            *season_of("qb", 2025, 6, position="QB", points=14.0),
            *season_of("wr", 2025, 6, position="WR", points=25.0),
        )

        assert [row.position for row in table["SEA"]] == ["QB", "WR"]

    def test_the_starter_is_the_quarterback_who_played_not_the_one_who_scored(self):
        """A backup with two big afternoons is not the starter, and putting him
        at the top of the table says he is."""
        table = splits(
            *season_of("starter", 2025, 8, position="QB", points=12.0),
            *season_of("backup", 2025, 2, position="QB", points=30.0),
        )

        assert table["SEA"][0].player == "STARTER"

    def test_a_team_with_no_quarterback_simply_has_one_row_fewer(self):
        table = splits(*season_of("wr", 2025, 5, position="WR"))

        assert [row.position for row in table["SEA"]] == ["WR"]

    def test_the_table_is_the_quarterback_plus_four(self):
        rows = [*season_of("qb", 2025, 6, position="QB", points=14.0)]
        for n in range(6):
            rows += season_of(f"wr{n}", 2025, 6, position="WR", points=20.0 - n)

        table = splits(*rows, limit=config.FANTASY_ROWS)

        assert len(table["SEA"]) == config.FANTASY_ROWS
        assert table["SEA"][0].position == "QB"

    def test_a_player_who_is_not_on_a_roster_today_is_dropped(self):
        """He may have had a fine season. He is not playing on Sunday."""
        table = splits(
            *season_of("here", 2025, 5),
            *season_of("gone", 2025, 5),
            current={"here": "SEA"},
        )

        assert [row.player for row in table["SEA"]] == ["HERE"]

    def test_only_scoring_positions_are_listed(self):
        table = splits(
            *season_of("wr", 2025, 5, position="WR"),
            *season_of("lb", 2025, 5, position="LB"),
        )

        assert [row.player for row in table["SEA"]] == ["WR"]


class TestKickers:
    def kicks(self, player_id, count, *, home, made, att, long=45, **over):
        return [
            week(
                player_id,
                week=n,
                home=home,
                position="K",
                fg_made=made,
                fg_att=att,
                fg_long=long,
                **over,
            )
            for n in range(1, count + 1)
        ]

    def test_it_emits_one_row_per_venue_home_first(self):
        table = kickers(
            *self.kicks("k1", 4, home=True, made=2, att=2),
            *[
                week("k1", week=n, home=False, position="K", fg_made=1, fg_att=2)
                for n in range(5, 9)
            ],
        )

        assert [row.venue for row in table["SEA"]] == ["home", "away"]

    def test_accuracy_is_a_fraction_not_a_percentage(self):
        """Every rate in this payload is a fraction between 0 and 1. The
        renderer multiplies; a percentage here would print 9100%."""
        table = kickers(*self.kicks("k1", 4, home=True, made=1, att=2))

        assert table["SEA"][0].fg_pct == pytest.approx(0.5)

    def test_it_carries_no_points_column(self):
        """nflverse scores every kicker 0.0 -- its formula excludes kicking --
        so a points number here would be a scoring rule we invented and the
        reader's league would disagree with it."""
        table = kickers(*self.kicks("k1", 4, home=True, made=2, att=2))
        fields = table["SEA"][0].model_dump()

        assert not [name for name in fields if "point" in name or "fantasy" in name]

    def test_a_kicker_signed_in_december_is_absent_rather_than_perfect(self):
        """Two-for-two is not a hundred percent of anything."""
        table = kickers(
            *self.kicks("k1", 2, home=True, made=1, att=1),
            min_att=config.KICKER_MIN_FG_ATT,
        )

        assert table == {}

    def test_the_long_is_the_longest_he_made_at_that_venue(self):
        table = kickers(
            week("k1", week=1, home=True, position="K", fg_made=1, fg_att=1, fg_long=52),
            week("k1", week=2, home=True, position="K", fg_made=1, fg_att=1, fg_long=39),
            week("k1", week=3, home=True, position="K", fg_made=1, fg_att=1, fg_long=44),
            week("k1", week=4, home=True, position="K", fg_made=1, fg_att=1, fg_long=41),
            week("k1", week=5, home=True, position="K", fg_made=1, fg_att=1, fg_long=48),
        )

        assert table["SEA"][0].fg_long == 52

    def test_the_team_carries_one_kicker_not_a_competition(self):
        table = kickers(
            *self.kicks("incumbent", 8, home=True, made=2, att=2),
            *self.kicks("replacement", 2, home=True, made=2, att=2),
        )

        assert [row.player for row in table["SEA"]] == ["INCUMBENT"]

    def test_attempts_per_game_are_per_game_played_at_that_venue(self):
        table = kickers(*self.kicks("k1", 4, home=True, made=2, att=3))
        row = table["SEA"][0]

        assert row.games == 4
        assert row.fg_att_per_game == pytest.approx(3.0)
