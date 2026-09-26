"""Quarterbacks.

Each rate is a count over a count, so the tests hold the denominators: what a
dropback is, which dropbacks a charting feed has actually seen, and which way
each rank runs.
"""

from __future__ import annotations

import polars as pl
import pytest

from pipeline.metrics import quarterbacks as qb
from pipeline.schema import QB_STATS
from pipeline.sources import charting as charting_source
from pipeline.sources import pbp as pbp_source
from pipeline.sources import snaps as snaps_source
from pipeline.sources.pbp import QB_COLUMNS, PlayByPlayUnavailable

PLAY_SCHEMA = {
    "season_type": pl.Utf8,
    "game_id": pl.Utf8,
    "play_id": pl.Float64,
    "posteam": pl.Utf8,
    "defteam": pl.Utf8,
    "play_type": pl.Utf8,
    "qb_dropback": pl.Float64,
    "two_point_attempt": pl.Float64,
    "passer_player_id": pl.Utf8,
    "passer_player_name": pl.Utf8,
    "rusher_player_id": pl.Utf8,
    "rusher_player_name": pl.Utf8,
    "sack": pl.Float64,
    "qb_scramble": pl.Float64,
    "complete_pass": pl.Float64,
    "passing_yards": pl.Float64,
    "air_yards": pl.Float64,
    "cpoe": pl.Float64,
}

_next_id = iter(range(1, 10_000))


def throw(
    qb_id="q1",
    *,
    game="g1",
    offense="SEA",
    defense="NE",
    complete=1,
    yards=10.0,
    air=8.0,
    cpoe=5.0,
    **over,
):
    """A pass attempt."""
    row = {
        "season_type": "REG",
        "game_id": game,
        "play_id": float(next(_next_id)),
        "posteam": offense,
        "defteam": defense,
        "play_type": "pass",
        "qb_dropback": 1.0,
        "two_point_attempt": 0.0,
        "passer_player_id": qb_id,
        "passer_player_name": qb_id.upper() if qb_id else None,
        "rusher_player_id": None,
        "rusher_player_name": None,
        "sack": 0.0,
        "qb_scramble": 0.0,
        "complete_pass": float(complete),
        "passing_yards": yards if complete else None,
        "air_yards": air,
        "cpoe": cpoe,
    }
    row.update(over)
    return row


def sack(qb_id="q1", **over):
    row = throw(qb_id, complete=0, yards=None, air=None, cpoe=None, sack=1.0)
    row.update(over)
    return row


def scramble(qb_id="q1", **over):
    """nflfastR leaves the passer blank on a scramble and names the rusher."""
    row = throw(None, complete=0, air=None, cpoe=None, play_type="run", qb_scramble=1.0)
    row.update(rusher_player_id=qb_id, rusher_player_name=qb_id.upper())
    row.update(over)
    return row


def plays(*rows):
    return pl.DataFrame(list(rows), schema=PLAY_SCHEMA)


def line(frame, by="qb_id", key="q1"):
    return {r[by]: r for r in qb._line(frame, by).iter_rows(named=True)}[key]


def test_the_fixture_schema_matches_the_column_guard():
    assert set(PLAY_SCHEMA) == set(QB_COLUMNS)


class TestDropbacks:
    def test_a_scramble_belongs_to_the_quarterback_who_ran(self):
        frame = qb.dropbacks(plays(scramble("q1")))
        assert frame["qb_id"].to_list() == ["q1"]
        assert frame["scramble"].to_list() == [True]
        assert frame["attempt"].to_list() == [False]

    def test_a_sack_is_a_dropback_but_not_an_attempt(self):
        frame = qb.dropbacks(plays(sack()))
        assert frame["attempt"].to_list() == [False]
        assert frame["sack"].to_list() == [True]

    def test_two_point_tries_and_the_postseason_are_left_out(self):
        frame = qb.dropbacks(
            plays(throw(), throw(two_point_attempt=1.0), throw(season_type="POST"))
        )
        assert frame.height == 1

    def test_a_designed_run_is_not_a_dropback(self):
        frame = qb.dropbacks(plays(throw(play_type="run", qb_dropback=0.0)))
        assert frame.height == 0


class TestLine:
    def test_volume_is_per_game_and_rates_are_per_dropback(self):
        frame = qb.dropbacks(
            plays(
                throw(game="g1"),
                throw(game="g1", complete=0),
                sack(game="g1"),
                scramble(game="g1"),
                throw(game="g2"),
                throw(game="g2"),
            )
        )
        row = line(frame)
        assert row["att"] == pytest.approx(2.0)  # 4 attempts over 2 games
        assert row["dropbacks"] == pytest.approx(3.0)
        assert row["sack_rate"] == pytest.approx(1 / 6)
        assert row["scramble_rate"] == pytest.approx(1 / 6)
        assert row["cmp_pct"] == pytest.approx(3 / 4)

    def test_yards_per_attempt_leaves_sacks_out_of_both_halves(self):
        frame = qb.dropbacks(plays(throw(yards=20.0), throw(complete=0), sack()))
        assert line(frame)["ypa"] == pytest.approx(10.0)

    def test_adot_and_cpoe_average_over_attempts_with_a_value(self):
        frame = qb.dropbacks(
            plays(
                throw(air=10.0, cpoe=4.0),
                throw(air=0.0, cpoe=-2.0),
                throw(air=None, cpoe=None),
                sack(),
            )
        )
        row = line(frame)
        assert row["adot"] == pytest.approx(5.0)
        assert row["cpoe"] == pytest.approx(1.0)

    def test_a_defense_line_is_every_quarterback_it_faced(self):
        frame = qb.dropbacks(plays(throw("q1", defense="NE"), sack("q2", defense="NE")))
        row = line(frame, "defteam", "NE")
        assert row["sack_rate"] == pytest.approx(0.5)
        assert row["games"] == 1


def blitz_table(frame, flags):
    """FTN's side of the join: one row per charted play."""
    return pl.DataFrame(
        {
            "game_id": frame["game_id"].to_list()[: len(flags)],
            "play_id": frame["play_id"].to_list()[: len(flags)],
            "n_blitzers": flags,
        },
        schema={"game_id": pl.Utf8, "play_id": pl.Int64, "n_blitzers": pl.Int64},
    )


class TestBlitz:
    def test_the_rate_counts_only_the_plays_ftn_has_charted(self):
        frame = qb.dropbacks(plays(throw(), throw(), throw(), throw()))
        # Two charted -- one blitz, one four-man rush -- and two not yet.
        frame = qb.with_blitzes(frame, blitz_table(frame, [2, 0]))
        assert line(frame)["blitz_rate"] == pytest.approx(0.5)

    def test_nothing_charted_is_no_rate_rather_than_zero(self):
        frame = qb.dropbacks(plays(throw()))
        assert line(frame)["blitz_rate"] is None

    def test_the_split_divides_his_charted_dropbacks(self):
        frame = qb.dropbacks(plays(throw(yards=30.0), sack(), throw(complete=0), throw(yards=6.0)))
        frame = qb.with_blitzes(frame, blitz_table(frame, [1, 1, 0]))
        blitz, calm = qb.split_rows(frame, "q1")
        assert (blitz.split, blitz.dropbacks, blitz.ypa, blitz.sack_rate) == ("blitz", 2, 30.0, 0.5)
        # The fourth dropback was never charted and is in neither half.
        assert (calm.split, calm.dropbacks, calm.cmp_pct) == ("no_blitz", 1, 0.0)

    def test_no_charted_dropbacks_is_no_split(self):
        frame = qb.dropbacks(plays(throw()))
        assert qb.split_rows(frame, "q1") == []


def pfr(*rows):
    return pl.DataFrame(
        [
            dict(
                zip(
                    ("game_id", "team", "opponent", "pfr_player_id", "times_pressured"),
                    r,
                    strict=True,
                )
            )
            for r in rows
        ],
        schema={
            "game_id": pl.Utf8,
            "team": pl.Utf8,
            "opponent": pl.Utf8,
            "pfr_player_id": pl.Utf8,
            "times_pressured": pl.Float64,
        },
    )


KEY = pl.DataFrame({"pfr_id": ["Q1pfr"], "player_id": ["q1"]})


class TestPressure:
    def test_only_the_games_pfr_has_charted_are_in_the_denominator(self):
        frame = qb.dropbacks(plays(throw(game="g1"), throw(game="g1"), throw(game="g2")))
        by_qb, by_defense = qb.pressure_rates(frame, pfr(("g1", "SEA", "NE", "Q1pfr", 1.0)), KEY)
        assert by_qb == {"q1": pytest.approx(0.5)}
        assert by_defense == {"NE": pytest.approx(0.5)}

    def test_a_passer_pfr_cannot_map_still_counts_against_the_defense(self):
        frame = qb.dropbacks(plays(throw("q1"), throw("q2")))
        by_qb, by_defense = qb.pressure_rates(
            frame,
            pfr(("g1", "SEA", "NE", "Q1pfr", 0.0), ("g1", "SEA", "NE", "Q2pfr", 1.0)),
            KEY,
        )
        assert by_qb == {"q1": 0.0}
        assert by_defense == {"NE": pytest.approx(0.5)}

    def test_pfr_team_codes_are_mapped(self):
        frame = qb.dropbacks(plays(throw(defense="ARI")))
        _, by_defense = qb.pressure_rates(frame, pfr(("g1", "SEA", "AZ", "Q1pfr", 1.0)), KEY)
        assert by_defense == {"ARI": 1.0}


class TestRanks:
    def table(self, **by_team):
        return {team: dict.fromkeys(QB_STATS, 0.0) | stats for team, stats in by_team.items()}

    def test_rank_one_allows_the_most(self):
        ranked = qb.ranks(self.table(AAA={"ypa": 6.0}, BBB={"ypa": 8.0}))
        assert (ranked["BBB"]["ypa"], ranked["AAA"]["ypa"]) == (1, 2)

    @pytest.mark.parametrize("key", ["sack_rate", "pressure_rate"])
    def test_sacks_and_pressure_are_inverted_so_one_still_favours_the_offense(self, key):
        ranked = qb.ranks(self.table(AAA={key: 0.1}, BBB={key: 0.3}))
        assert (ranked["AAA"][key], ranked["BBB"][key]) == (1, 2)

    def test_neutral_stats_rank_most_first(self):
        ranked = qb.ranks(self.table(AAA={"blitz_rate": 0.2}, BBB={"blitz_rate": 0.4}))
        assert ranked["BBB"]["blitz_rate"] == 1
        assert {"scramble_rate", "adot", "blitz_rate"} == qb.NEUTRAL

    def test_a_defense_with_no_value_is_unranked_and_the_rest_close_up(self):
        ranked = qb.ranks(
            self.table(
                AAA={"pressure_rate": None}, BBB={"pressure_rate": 0.3}, CCC={"pressure_rate": 0.2}
            )
        )
        assert ranked["AAA"]["pressure_rate"] is None
        assert (ranked["CCC"]["pressure_rate"], ranked["BBB"]["pressure_rate"]) == (1, 2)

    def test_ties_break_on_the_abbreviation_so_reruns_agree(self):
        ranked = qb.ranks(self.table(BBB={"ypa": 7.0}, AAA={"ypa": 7.0}))
        assert (ranked["AAA"]["ypa"], ranked["BBB"]["ypa"]) == (1, 2)


class TestStarters:
    def test_the_starter_has_the_most_dropbacks(self):
        frame = qb.dropbacks(plays(throw("q1"), throw("q2"), throw("q2")))
        assert qb.starters(frame, {"q1": "SEA", "q2": "SEA"}) == {"SEA": "q2"}

    def test_he_is_listed_under_the_team_he_is_on_now(self):
        frame = qb.dropbacks(plays(throw("q1", offense="MIN")))
        assert qb.starters(frame, {"q1": "SEA"}) == {"SEA": "q1"}

    def test_a_quarterback_off_every_roster_is_dropped(self):
        frame = qb.dropbacks(plays(throw("q1"), throw("q1"), throw("q2")))
        assert qb.starters(frame, {"q2": "SEA"}) == {"SEA": "q2"}

    def test_his_row_carries_every_stat_and_his_pressure(self):
        frame = qb.dropbacks(plays(throw("q1")))
        row = qb.quarterback_rows(frame, {"q1": 0.25}, {"SEA": "q1"})["SEA"]
        assert (row.player, row.games) == ("Q1", 1)
        assert list(row.stats) == list(QB_STATS)
        assert row.stats["pressure_rate"] == 0.25


class TestSide:
    def test_an_offense_meets_the_other_teams_defense(self):
        frame = qb.dropbacks(plays(throw("q1", offense="SEA", defense="NE")))
        tables = qb.QbTables(
            quarterbacks=qb.quarterback_rows(frame, {}, {"SEA": "q1"}),
            defenses=qb.defense_rows(frame, {}),
        )
        got = qb.side(tables, offense="SEA", defense="NE")
        assert got.quarterback.player == "Q1"
        assert got.defense.team == "NE"
        assert got.defense.stats["pressure_rate"].value is None
        assert got.defense.stats["pressure_rate"].rank is None

    def test_nothing_on_either_side_is_no_side(self):
        assert qb.side(qb.QbTables(), offense="SEA", defense="NE") is None


class TestBuild:
    @pytest.fixture
    def feeds(self, monkeypatch):
        frame = plays(throw("q1"), sack("q1"))
        monkeypatch.setattr(pbp_source, "load", lambda season: frame)
        monkeypatch.setattr(snaps_source, "current_teams", lambda season: {"q1": "SEA"})
        monkeypatch.setattr(snaps_source, "player_key", lambda season: KEY)
        return frame

    def test_a_dead_charting_feed_costs_its_column_and_says_so(self, feeds, monkeypatch):
        def dead(season):
            raise charting_source.ChartingUnavailable("gone")

        monkeypatch.setattr(charting_source, "blitzes", dead)
        monkeypatch.setattr(charting_source, "pressures", dead)

        tables = qb.build(2026, 2026)

        assert len(tables.warnings) == 2
        row = tables.quarterbacks["SEA"]
        assert row.stats["blitz_rate"] is None
        assert row.stats["pressure_rate"] is None
        assert row.stats["sack_rate"] == 0.5
        assert tables.splits["SEA"] == []

    def test_every_feed_up_fills_every_column(self, feeds, monkeypatch):
        ids = qb.dropbacks(feeds)
        monkeypatch.setattr(charting_source, "blitzes", lambda season: blitz_table(ids, [1, 0]))
        monkeypatch.setattr(
            charting_source, "pressures", lambda season: pfr(("g1", "SEA", "NE", "Q1pfr", 1.0))
        )

        tables = qb.build(2026, 2026)

        assert tables.warnings == []
        row = tables.quarterbacks["SEA"]
        assert row.stats["blitz_rate"] == 0.5
        assert row.stats["pressure_rate"] == 0.5
        assert tables.defenses["NE"].stats["pressure_rate"].rank == 1


class TestColumnGuard:
    def test_a_moved_column_fails_loudly_and_names_it(self):
        with pytest.raises(PlayByPlayUnavailable, match="cpoe"):
            qb.dropbacks(plays(throw()).drop("cpoe"))
