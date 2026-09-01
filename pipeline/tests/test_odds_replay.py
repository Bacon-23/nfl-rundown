"""Record and replay.

The load-bearing test here is the stale-fixture one. Replay joins events to
games by team pair, so a fixture from another week matches nothing, every game
falls back to nflverse lines, and a broken parser still produces a green run.
The guard exists to make that failure loud, so it needs a test that proves the
guard actually fires.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from pipeline import config
from pipeline.sources import odds as odds_source
from pipeline.sources.odds_tape import (
    ReplayTape,
    StaleFixtureError,
    tape_key,
)
from pipeline.tests.test_odds import BULK_URL, bulk_event, make_game


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "secret-key-do-not-record")


@pytest.fixture(autouse=True)
def _no_team_totals(monkeypatch):
    monkeypatch.setattr(config, "ODDS_FETCH_TEAM_TOTALS", False)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


@respx.mock
def test_record_then_replay_gives_identical_odds(tmp_path):
    fixture = tmp_path / "odds.json"

    respx.get(BULK_URL).mock(
        return_value=httpx.Response(
            200,
            json=[bulk_event(spread=-4.5, total=45.5)],
            headers={"x-requests-remaining": "98765"},
        )
    )

    live = odds_source.fetch([make_game()], record=fixture)
    assert fixture.is_file()

    # Replay must not touch the network. Any request now would 404 through
    # respx, so a passing assertion proves nothing went out.
    respx.get(BULK_URL).mock(side_effect=AssertionError("replay hit the network"))

    replayed = odds_source.fetch([make_game()], replay=fixture)

    assert replayed.source == "odds_api"
    assert replayed.odds["2026_01_NE_SEA"] == live.odds["2026_01_NE_SEA"]
    assert replayed.quota_remaining == 98765


@respx.mock
def test_fixture_never_contains_the_api_key(tmp_path):
    """Fixtures are committed to the repo, so this is a real leak risk."""
    fixture = tmp_path / "odds.json"

    respx.get(BULK_URL).mock(return_value=httpx.Response(200, json=[bulk_event()]))
    odds_source.fetch([make_game()], record=fixture)

    text = fixture.read_text(encoding="utf-8")
    assert "secret-key-do-not-record" not in text
    assert "apiKey" not in text


@respx.mock
def test_a_failed_fetch_writes_no_fixture(tmp_path):
    """A half-recorded fixture must never reach the repo."""
    fixture = tmp_path / "odds.json"

    respx.get(BULK_URL).mock(return_value=httpx.Response(429))

    result = odds_source.fetch([make_game()], record=fixture)

    assert result.source == "nflverse_fallback"
    assert not fixture.exists()


@respx.mock
def test_recording_captures_per_event_calls_too(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ODDS_FETCH_TEAM_TOTALS", True)
    fixture = tmp_path / "odds.json"

    respx.get(BULK_URL).mock(return_value=httpx.Response(200, json=[bulk_event()]))
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
                                    {
                                        "name": "Over",
                                        "description": "Seattle Seahawks",
                                        "point": 26.5,
                                        "price": -110,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
    )

    odds_source.fetch([make_game()], record=fixture)

    entries = json.loads(fixture.read_text(encoding="utf-8"))["entries"]
    assert any("/events/evt1/odds" in key for key in entries)

    respx.get(BULK_URL).mock(side_effect=AssertionError("replay hit the network"))
    respx.get(url__regex=r".*/events/.*").mock(
        side_effect=AssertionError("replay hit the network")
    )

    replayed = odds_source.fetch([make_game()], replay=fixture)
    assert replayed.odds["2026_01_NE_SEA"].home_team_total == 26.5


# ---------------------------------------------------------------------------
# The stale-fixture guard
# ---------------------------------------------------------------------------


@respx.mock
def test_stale_fixture_fails_rather_than_falling_back(tmp_path):
    """A fixture whose games no longer exist must stop the run.

    Without the guard this run would return nflverse lines and look fine.
    """
    fixture = tmp_path / "odds.json"

    respx.get(BULK_URL).mock(return_value=httpx.Response(200, json=[bulk_event()]))
    odds_source.fetch([make_game()], record=fixture)

    # Next week's slate: none of these teams are in the fixture.
    next_week = [
        make_game(away="GB", home="MIN", game_id="2026_02_GB_MIN"),
        make_game(away="DAL", home="NYG", game_id="2026_02_DAL_NYG"),
    ]

    with pytest.raises(StaleFixtureError, match="0/2"):
        odds_source.fetch(next_week, replay=fixture)


@respx.mock
def test_partial_match_above_the_floor_is_allowed(tmp_path):
    """Books legitimately lag on a game or two; that is not a stale fixture."""
    fixture = tmp_path / "odds.json"

    respx.get(BULK_URL).mock(return_value=httpx.Response(200, json=[bulk_event()]))
    odds_source.fetch([make_game()], record=fixture)

    games = [make_game(), make_game(away="GB", home="MIN", game_id="2026_01_GB_MIN")]

    result = odds_source.fetch(games, replay=fixture)

    assert result.source == "odds_api"
    assert result.odds["2026_01_NE_SEA"].spread == -3.5
    # The unmatched game still gets a line, from nflverse.
    assert result.odds["2026_01_GB_MIN"].spread == -3.5


@respx.mock
def test_the_floor_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPLAY_MIN_MATCH_RATE", 0.99)

    fixture = tmp_path / "odds.json"
    respx.get(BULK_URL).mock(return_value=httpx.Response(200, json=[bulk_event()]))
    odds_source.fetch([make_game()], record=fixture)

    games = [make_game(), make_game(away="GB", home="MIN", game_id="2026_01_GB_MIN")]

    with pytest.raises(StaleFixtureError, match="1/2"):
        odds_source.fetch(games, replay=fixture)


def test_live_runs_are_never_subject_to_the_guard():
    """The floor applies to replay only; a quiet book must not fail production."""
    result = odds_source.fetch([make_game()], use_api=False)
    assert result.source == "nflverse_fallback"


# ---------------------------------------------------------------------------
# Tape mechanics
# ---------------------------------------------------------------------------


def test_missing_fixture_is_a_clear_error(tmp_path):
    with pytest.raises(StaleFixtureError, match="record-odds"):
        ReplayTape(tmp_path / "nope.json")


def test_empty_fixture_is_rejected(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"recorded_at": "x", "entries": {}}), encoding="utf-8")

    with pytest.raises(StaleFixtureError, match="no responses"):
        ReplayTape(path)


def test_tape_key_ignores_the_credential():
    """Otherwise every fixture would break on key rotation."""
    base = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"

    first = tape_key(base, {"apiKey": "aaa", "markets": "h2h", "bookmakers": "draftkings"})
    second = tape_key(base, {"apiKey": "bbb", "markets": "h2h", "bookmakers": "draftkings"})

    assert first == second
    assert "aaa" not in first


def test_tape_key_separates_events():
    base = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events"
    one = tape_key(f"{base}/evt1/odds", {"markets": "team_totals"})
    two = tape_key(f"{base}/evt2/odds", {"markets": "team_totals"})

    assert one != two


def test_record_and_replay_together_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Cannot record and replay"):
        odds_source.fetch([make_game()], record=tmp_path / "a.json", replay=tmp_path / "b.json")
