"""Weather, venues, and the forecast horizon.

The load-bearing test is the neutral-site one. nflverse's `stadium_id` and
`roof` both follow the *home team's* stadium, so a game at the Melbourne
Cricket Ground is labelled a dome because SoFi is one. Trusting that column
would forecast Los Angeles weather for a game in Australia.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import httpx
import respx

from pipeline import config
from pipeline.sources import weather as weather_source
from pipeline.sources.venues import lookup
from pipeline.tests.test_odds import make_game

METEO = config.OPEN_METEO_URL


def at(days: int) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(
        minute=0, second=0, microsecond=0
    )


def game(stadium="Lumen Field", roof="outdoors", days=3, game_id="2026_01_NE_SEA"):
    return dataclasses.replace(
        make_game(game_id=game_id),
        stadium=stadium,
        roof=roof,
        kickoff_utc=at(days),
    )


def forecast_response(kickoff: datetime, *, temp=68.0, precip=5, wind=4.0, code=0):
    """An Open-Meteo payload with a single usable hour at kickoff."""
    stamp = kickoff.strftime("%Y-%m-%dT%H:00")
    return httpx.Response(
        200,
        json={
            "hourly": {
                "time": [stamp],
                "temperature_2m": [temp],
                "precipitation_probability": [precip],
                "wind_speed_10m": [wind],
                "weather_code": [code],
            }
        },
    )


# ---------------------------------------------------------------------------
# Venue table vs. the schedule's roof column
# ---------------------------------------------------------------------------


class TestVenueLookup:
    def test_known_stadium_resolves(self):
        venue = lookup("Lumen Field")
        assert venue is not None
        assert venue.roof == "outdoor"

    def test_matching_tolerates_punctuation_and_case(self):
        assert lookup("us bank stadium") is lookup("U.S. Bank Stadium")

    def test_unknown_stadium_is_none_not_an_error(self):
        """A stadium rename should cost one cell, not a whole build."""
        assert lookup("Some New Ballpark") is None

    def test_retractable_is_not_treated_as_indoors(self):
        """A retractable roof is a game-day decision, not a fact about the venue."""
        venue = lookup("AT&T Stadium")
        assert venue.roof == "retractable"
        assert venue.is_indoors is False


@respx.mock
def test_neutral_site_ignores_the_schedules_inherited_roof():
    """The bug this table exists to prevent.

    nflverse reports the Melbourne Cricket Ground as a dome because the home
    team plays at SoFi. It is an open-air ground and must get a forecast.
    """
    kickoff = at(3)
    route = respx.get(METEO).mock(return_value=forecast_response(kickoff, temp=52.0))

    mcg = game(stadium="Melbourne Cricket Ground", roof="dome")
    results, warnings = weather_source.fetch([mcg])

    assert route.called
    called_params = route.calls[0].request.url.params
    assert float(called_params["latitude"]) < 0, "should query the southern hemisphere"

    weather = results[mcg.game_id]
    assert weather.is_indoors is False
    assert "52" in weather.summary
    assert warnings == []


@respx.mock
def test_a_real_dome_skips_the_network_entirely():
    ford = game(stadium="Ford Field", roof="dome")
    results, _ = weather_source.fetch([ford])

    assert not respx.calls
    assert results[ford.game_id].is_indoors is True
    assert "Indoors" in results[ford.game_id].summary


@respx.mock
def test_unknown_venue_falls_back_to_the_schedule_and_warns():
    unknown = game(stadium="Brand New Stadium", roof="dome")
    results, warnings = weather_source.fetch([unknown])

    assert results[unknown.game_id].is_indoors is True
    assert warnings == []

    outdoor = game(stadium="Brand New Stadium", roof="outdoors")
    results, warnings = weather_source.fetch([outdoor])

    assert results[outdoor.game_id].summary == "Weather unavailable"
    assert any("add it to venues.py" in w for w in warnings)


# ---------------------------------------------------------------------------
# Forecast horizon
# ---------------------------------------------------------------------------


@respx.mock
def test_beyond_the_horizon_is_tbd_not_a_guess():
    far = game(days=config.WEATHER_HORIZON_DAYS + 5)
    results, warnings = weather_source.fetch([far])

    assert not respx.calls, "should not even ask about a game a month out"
    assert results[far.game_id].summary == "TBD"
    assert warnings == []


@respx.mock
def test_kickoff_hour_missing_from_the_response_is_tbd():
    kickoff = at(3)
    respx.get(METEO).mock(return_value=forecast_response(kickoff + timedelta(days=9)))

    soon = game(days=3)
    results, _ = weather_source.fetch([soon])

    assert results[soon.game_id].summary == "TBD"


@respx.mock
def test_an_api_failure_degrades_one_game_and_warns():
    respx.get(METEO).mock(return_value=httpx.Response(500))

    soon = game(days=3)
    results, warnings = weather_source.fetch([soon])

    assert results[soon.game_id].summary == "Weather unavailable"
    assert any("weather lookup failed" in w for w in warnings)


@respx.mock
def test_a_network_error_does_not_take_down_the_build():
    respx.get(METEO).mock(side_effect=httpx.ConnectError("dns"))

    soon = game(days=3)
    results, warnings = weather_source.fetch([soon])

    assert results[soon.game_id].summary == "Weather unavailable"
    assert warnings


# ---------------------------------------------------------------------------
# Summary wording
# ---------------------------------------------------------------------------


class TestSummary:
    @respx.mock
    def _summary(self, **kwargs) -> str:
        kickoff = at(3)
        respx.get(METEO).mock(return_value=forecast_response(kickoff, **kwargs))
        results, _ = weather_source.fetch([game(days=3)])
        return results["2026_01_NE_SEA"].summary

    def test_clear_is_labelled_despite_being_weather_code_zero(self):
        """Code 0 is falsy. Testing truthiness would silently drop the label."""
        assert "clear" in self._summary(code=0).lower()

    def test_rain_is_called_out(self):
        summary = self._summary(code=63, precip=70)
        assert "rain" in summary.lower()
        assert "70% precip" in summary

    def test_light_wind_is_not_mentioned(self):
        assert "wind" not in self._summary(wind=4.0)

    def test_meaningful_wind_is_mentioned(self):
        assert "wind 18 mph" in self._summary(wind=18.0)

    def test_a_dry_forecast_says_so(self):
        assert "no rain concern" in self._summary(code=0, precip=0)

    def test_retractable_roofs_are_flagged(self):
        kickoff = at(3)
        with respx.mock:
            respx.get(METEO).mock(return_value=forecast_response(kickoff))
            results, _ = weather_source.fetch(
                [game(stadium="Lucas Oil Stadium", roof="dome", days=3)]
            )
        assert "(retractable roof)" in results["2026_01_NE_SEA"].summary
