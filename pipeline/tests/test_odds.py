"""Odds parsing, team-total derivation, and every failure path.

These run against recorded HTTP fixtures, so CI needs no Odds API key and
burns no credits.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from pipeline import config
from pipeline.sources import odds as odds_source
from pipeline.sources.schedule import ScheduledGame
from pipeline.sources.team_map import UnmappedTeamError

BULK_URL = f"{config.ODDS_API_BASE}/sports/{config.ODDS_SPORT_KEY}/odds"


def make_game(
    away="NE",
    home="SEA",
    spread_line=3.5,
    total_line=44.5,
    game_id="2026_01_NE_SEA",
) -> ScheduledGame:
    return ScheduledGame(
        game_id=game_id,
        season=2026,
        week=1,
        away=away,
        home=home,
        kickoff_utc=datetime(2026, 9, 10, 0, 20, tzinfo=UTC),
        roof="outdoors",
        surface="fieldturf",
        stadium="Lumen Field",
        stadium_id="SEA00",
        div_game=False,
        spread_line=spread_line,
        total_line=total_line,
        away_moneyline=154,
        home_moneyline=-185,
        away_score=None,
        home_score=None,
    )


def bulk_event(spread=-3.5, total=44.5, event_id="evt1"):
    """One event in the shape the bulk endpoint returns."""
    return {
        "id": event_id,
        "commence_time": "2026-09-10T00:20:00Z",
        "home_team": "Seattle Seahawks",
        "away_team": "New England Patriots",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Seattle Seahawks", "price": -110, "point": spread},
                            {"name": "New England Patriots", "price": -110, "point": -spread},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -110, "point": total},
                            {"name": "Under", "price": -110, "point": total},
                        ],
                    },
                ],
            }
        ],
    }


def _tt(side: str, team: str, point: float) -> dict:
    """One outcome of the team_totals market."""
    return {"name": side, "description": team, "point": point, "price": -110}


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _no_team_totals(monkeypatch):
    """Default the per-event call off; the tests that want it turn it on."""
    monkeypatch.setattr(config, "ODDS_FETCH_TEAM_TOTALS", False)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@respx.mock
def test_parses_spread_total_and_favorite():
    respx.get(BULK_URL).mock(
        return_value=httpx.Response(
            200, json=[bulk_event()], headers={"x-requests-remaining": "99000"}
        )
    )

    result = odds_source.fetch([make_game()])
    odds = result.odds["2026_01_NE_SEA"]

    assert result.source == "odds_api"
    assert result.quota_remaining == 99000
    assert odds.spread == -3.5
    assert odds.spread_favorite == "SEA"
    assert odds.total == 44.5
    assert odds.book_label == "DraftKings"


@respx.mock
def test_away_favorite_is_read_from_the_side_laying_points():
    # Home +6.5 means the away team is the favorite.
    respx.get(BULK_URL).mock(return_value=httpx.Response(200, json=[bulk_event(spread=6.5)]))

    odds = odds_source.fetch([make_game()]).odds["2026_01_NE_SEA"]

    assert odds.spread == -6.5
    assert odds.spread_favorite == "NE"


@respx.mock
def test_pickem_is_not_mistaken_for_a_missing_spread():
    respx.get(BULK_URL).mock(return_value=httpx.Response(200, json=[bulk_event(spread=0)]))

    odds = odds_source.fetch([make_game()]).odds["2026_01_NE_SEA"]

    assert odds.spread == 0.0
    assert odds.spread_favorite is not None


# ---------------------------------------------------------------------------
# Team totals
# ---------------------------------------------------------------------------


@respx.mock
def test_team_totals_derive_when_no_market_is_posted():
    """The mockup's numbers: 45.5 total, favorite -4.5, gives 25.0 and 20.5."""
    respx.get(BULK_URL).mock(
        return_value=httpx.Response(200, json=[bulk_event(spread=-4.5, total=45.5)])
    )

    odds = odds_source.fetch([make_game()]).odds["2026_01_NE_SEA"]

    assert odds.home_team_total == 25.0
    assert odds.away_team_total == 20.5
    assert odds.team_totals_derived is True


@respx.mock
def test_derived_team_totals_favor_the_away_team_when_it_is_favored():
    respx.get(BULK_URL).mock(
        return_value=httpx.Response(200, json=[bulk_event(spread=4.5, total=45.5)])
    )

    odds = odds_source.fetch([make_game()]).odds["2026_01_NE_SEA"]

    assert odds.away_team_total == 25.0
    assert odds.home_team_total == 20.5


@respx.mock
def test_posted_team_totals_win_over_derivation(monkeypatch):
    monkeypatch.setattr(config, "ODDS_FETCH_TEAM_TOTALS", True)

    respx.get(BULK_URL).mock(
        return_value=httpx.Response(200, json=[bulk_event(spread=-4.5, total=45.5)])
    )
    respx.get(url__regex=r".*/events/evt1/odds.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "evt1",
                "home_team": "Seattle Seahawks",
                "away_team": "New England Patriots",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "markets": [
                            {
                                "key": "team_totals",
                                "outcomes": [
                                    _tt("Over", "Seattle Seahawks", 26.5),
                                    _tt("Under", "Seattle Seahawks", 26.5),
                                    _tt("Over", "New England Patriots", 19.5),
                                ],
                            }
                        ],
                    }
                ],
            },
        )
    )

    odds = odds_source.fetch([make_game()]).odds["2026_01_NE_SEA"]

    assert odds.home_team_total == 26.5
    assert odds.away_team_total == 19.5
    assert odds.team_totals_derived is False


@respx.mock
def test_team_total_failure_falls_back_to_derivation(monkeypatch):
    """A per-event 500 must not lose the whole build."""
    monkeypatch.setattr(config, "ODDS_FETCH_TEAM_TOTALS", True)

    respx.get(BULK_URL).mock(
        return_value=httpx.Response(200, json=[bulk_event(spread=-4.5, total=45.5)])
    )
    respx.get(url__regex=r".*/events/evt1/odds.*").mock(return_value=httpx.Response(500))

    result = odds_source.fetch([make_game()])
    odds = result.odds["2026_01_NE_SEA"]

    assert result.source == "odds_api"
    assert odds.home_team_total == 25.0
    assert odds.team_totals_derived is True


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


@respx.mock
def test_quota_exhausted_falls_back_to_nflverse():
    respx.get(BULK_URL).mock(return_value=httpx.Response(429, json={"message": "quota"}))

    result = odds_source.fetch([make_game()])
    odds = result.odds["2026_01_NE_SEA"]

    assert result.source == "nflverse_fallback"
    assert odds.spread == -3.5
    assert odds.spread_favorite == "SEA"
    assert odds.total == 44.5
    assert any("429" in w for w in result.warnings)


@respx.mock
def test_bad_key_falls_back_rather_than_raising():
    respx.get(BULK_URL).mock(return_value=httpx.Response(401))

    result = odds_source.fetch([make_game()])

    assert result.source == "nflverse_fallback"
    assert any("401" in w for w in result.warnings)


@respx.mock
def test_network_error_falls_back():
    respx.get(BULK_URL).mock(side_effect=httpx.ConnectError("dns"))

    result = odds_source.fetch([make_game()])

    assert result.source == "nflverse_fallback"


def test_missing_key_falls_back(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)

    result = odds_source.fetch([make_game()])

    assert result.source == "nflverse_fallback"
    assert any("ODDS_API_KEY" in w for w in result.warnings)


@respx.mock
def test_unmappable_team_is_a_hard_error():
    """A book name we cannot resolve must stop the run, not drop the game."""
    event = bulk_event()
    event["home_team"] = "Seattle Kraken"

    respx.get(BULK_URL).mock(return_value=httpx.Response(200, json=[event]))

    with pytest.raises(UnmappedTeamError):
        odds_source.fetch([make_game()])


@respx.mock
def test_game_absent_from_the_response_keeps_a_line():
    """A game the book has not posted still renders, via the fallback."""
    respx.get(BULK_URL).mock(return_value=httpx.Response(200, json=[]))

    result = odds_source.fetch([make_game()])
    odds = result.odds["2026_01_NE_SEA"]

    assert result.source == "odds_api"
    assert odds.spread == -3.5
    assert any("no draftkings event" in w for w in result.warnings)


@respx.mock
def test_low_quota_raises_a_warning():
    respx.get(BULK_URL).mock(
        return_value=httpx.Response(
            200, json=[bulk_event()], headers={"x-requests-remaining": "12"}
        )
    )

    result = odds_source.fetch([make_game()])

    assert any("quota low" in w for w in result.warnings)


@respx.mock
def test_zero_quota_is_reported_not_swallowed():
    """Zero is falsy; it must still be reported rather than read as unknown."""
    respx.get(BULK_URL).mock(
        return_value=httpx.Response(
            200, json=[bulk_event()], headers={"x-requests-remaining": "0"}
        )
    )

    result = odds_source.fetch([make_game()])

    assert result.quota_remaining == 0
    assert any("quota low" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# nflverse fallback arithmetic
# ---------------------------------------------------------------------------


def test_fallback_translates_home_relative_spread_for_an_away_favorite():
    """nflverse spread_line is negative when the away team is favored."""
    result = odds_source.fetch([make_game(spread_line=-6.0)], use_api=False)
    odds = result.odds["2026_01_NE_SEA"]

    assert odds.spread == -6.0
    assert odds.spread_favorite == "NE"
    assert odds.away_team_total == 25.0
    assert odds.home_team_total == 19.0


def test_fallback_survives_a_game_with_no_line():
    result = odds_source.fetch(
        [make_game(spread_line=None, total_line=None)], use_api=False
    )
    odds = result.odds["2026_01_NE_SEA"]

    assert odds.spread is None
    assert odds.home_team_total is None
