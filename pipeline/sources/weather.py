"""Kickoff weather from Open-Meteo.

Free, no API key, no attribution requirement. One request per outdoor venue per
build; indoor games skip the network entirely.

Open-Meteo forecasts roughly 16 days ahead. Beyond that the honest answer is
"TBD" -- a page that invents a temperature for a game three weeks out is worse
than one that admits it does not know.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx

from pipeline import config
from pipeline.schema import Weather
from pipeline.sources.schedule import ScheduledGame
from pipeline.sources.venues import Venue, lookup

log = logging.getLogger(__name__)

#: WMO weather codes, collapsed to the distinctions a bettor actually cares
#: about. The full table has 28 values that mostly split hair-fine categories
#: of drizzle.
_CONDITIONS: dict[int, str] = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Freezing fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy rain showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorms",
    96: "Thunderstorms with hail",
    99: "Thunderstorms with hail",
}


#: Conditions dry enough that "no rain concern" is a fair thing to print.
_DRY_CONDITIONS = {"Clear", "Mostly clear", "Partly cloudy"}


def fetch(games: list[ScheduledGame]) -> tuple[dict[str, Weather], list[str]]:
    """Weather for every game, keyed by game_id.

    Never raises. A weather failure degrades one cell of one game; it must not
    take down a build.
    """
    results: dict[str, Weather] = {}
    warnings: list[str] = []

    with httpx.Client(
        timeout=config.HTTP_TIMEOUT,
        headers={"User-Agent": config.USER_AGENT},
    ) as client:
        for game in games:
            weather, warning = _for_game(client, game)
            if weather:
                results[game.game_id] = weather
            if warning:
                warnings.append(warning)

    return results, warnings


def _for_game(client: httpx.Client, game: ScheduledGame) -> tuple[Weather | None, str | None]:
    venue = lookup(game.stadium)

    if venue is None:
        # Fall back to the schedule's roof flag, which is right for ordinary
        # home games and only wrong at neutral sites.
        if game.is_indoors:
            return _indoors(), None
        return (
            Weather(summary="Weather unavailable", is_indoors=False),
            f"{game.game_id}: unknown venue {game.stadium!r} -- add it to venues.py.",
        )

    if venue.is_indoors:
        return _indoors(), None

    horizon = datetime.now(UTC) + timedelta(days=config.WEATHER_HORIZON_DAYS)
    if game.kickoff_utc > horizon:
        return Weather(summary="TBD", is_indoors=False), None

    try:
        forecast = _forecast(client, venue, game.kickoff_utc)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.debug("Weather lookup failed for %s: %s", game.game_id, exc)
        return (
            Weather(summary="Weather unavailable", is_indoors=False),
            f"{game.game_id}: weather lookup failed ({exc}).",
        )

    if forecast is None:
        return Weather(summary="TBD", is_indoors=False), None

    return _describe(forecast, venue), None


def _indoors() -> Weather:
    return Weather(summary="Indoors, no weather factor", is_indoors=True)


def _forecast(client: httpx.Client, venue: Venue, kickoff: datetime) -> dict | None:
    """Hourly conditions at the hour nearest kickoff, or None if out of range."""
    response = client.get(
        config.OPEN_METEO_URL,
        params={
            "latitude": venue.latitude,
            "longitude": venue.longitude,
            "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,weather_code",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "timezone": "UTC",
            # start_date/end_date reject anything outside the model's range, so
            # ask for the full window and pick the hour ourselves.
            "forecast_days": config.WEATHER_HORIZON_DAYS,
        },
    )
    response.raise_for_status()

    hourly = response.json()["hourly"]
    times = hourly["time"]

    index = _nearest_hour(times, kickoff)
    if index is None:
        return None

    return {
        "temp_f": hourly["temperature_2m"][index],
        "precip": hourly["precipitation_probability"][index],
        "wind": hourly["wind_speed_10m"][index],
        "code": hourly["weather_code"][index],
    }


def _nearest_hour(times: list[str], kickoff: datetime) -> int | None:
    """Index of the forecast hour closest to kickoff, within an hour of it."""
    target = kickoff.replace(minute=0, second=0, microsecond=0)
    stamp = target.strftime("%Y-%m-%dT%H:00")

    try:
        return times.index(stamp)
    except ValueError:
        return None


def _describe(forecast: dict, venue: Venue) -> Weather:
    """Turn a forecast into the one line the odds bar shows."""
    temp = _as_int(forecast["temp_f"])
    wind = _as_int(forecast["wind"])
    precip = _as_int(forecast["precip"])

    # WMO code 0 is "Clear" -- the most common condition there is. Testing it
    # for truthiness rather than for None would drop the label on exactly the
    # days it matters least to be wrong about, and never fail loudly.
    code = _as_int(forecast["code"])
    condition = _CONDITIONS.get(code, "") if code is not None else ""

    parts: list[str] = []

    if temp is not None:
        parts.append(f"{temp}°F")
    if condition:
        parts.append(condition.lower() if parts else condition)

    # Wind only matters to a football game once it is actually noticeable.
    if wind is not None and wind >= 12:
        parts.append(f"wind {wind} mph")

    if precip is not None and precip >= 30:
        parts.append(f"{precip}% precip")
    elif precip is not None and precip < 15 and condition in _DRY_CONDITIONS:
        parts.append("no rain concern")

    summary = ", ".join(parts) if parts else "Weather unavailable"

    if venue.roof == "retractable":
        summary = f"{summary} (retractable roof)"

    return Weather(
        summary=summary,
        temp_f=temp,
        wind_mph=wind,
        precip_chance=precip,
        is_indoors=False,
    )


def _as_int(value) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None
