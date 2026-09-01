"""Stadium coordinates and roof types, keyed by venue name.

**Not keyed on nflverse's `stadium_id`, deliberately.** That column carries the
*home team's* stadium, not the venue actually being played in. For the 2026
season `LAX01` appears against both SoFi Stadium and the Melbourne Cricket
Ground, and `JAX00` against EverBank, Wembley, and Tottenham. Keying on it
would forecast Los Angeles weather for a game in Australia.

The same inheritance corrupts nflverse's `roof` column: the Melbourne Cricket
Ground, an open-air ground, is reported as `dome` because SoFi is one. So this
table -- not the schedule -- is the authority on whether a game is played
indoors.

Coordinates are the playing surface, to about a hundred metres, which is far
finer than any weather model's grid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RoofType = Literal["outdoor", "dome", "retractable"]


@dataclass(frozen=True)
class Venue:
    name: str
    latitude: float
    longitude: float
    roof: RoofType

    @property
    def is_indoors(self) -> bool:
        """Fixed roofs only.

        A retractable roof is not an answer -- it is a game-day decision, so
        those venues still get a forecast and a caveat.
        """
        return self.roof == "dome"


def _v(name: str, lat: float, lon: float, roof: RoofType) -> tuple[str, Venue]:
    return name, Venue(name, lat, lon, roof)


#: Every venue on the 2026 schedule, domestic and international.
VENUES: dict[str, Venue] = dict(
    [
        # --- AFC East ---
        _v("Highmark Stadium", 42.7738, -78.7870, "outdoor"),
        _v("Hard Rock Stadium", 25.9580, -80.2389, "outdoor"),
        _v("Gillette Stadium", 42.0909, -71.2643, "outdoor"),
        _v("MetLife Stadium", 40.8135, -74.0745, "outdoor"),
        # --- AFC North ---
        _v("M&T Bank Stadium", 39.2780, -76.6227, "outdoor"),
        _v("Paycor Stadium", 39.0955, -84.5161, "outdoor"),
        _v("Huntington Bank Field", 41.5061, -81.6995, "outdoor"),
        _v("Acrisure Stadium", 40.4468, -80.0158, "outdoor"),
        # --- AFC South ---
        _v("Reliant Stadium", 29.6847, -95.4107, "retractable"),
        _v("NRG Stadium", 29.6847, -95.4107, "retractable"),
        _v("Lucas Oil Stadium", 39.7601, -86.1639, "retractable"),
        _v("EverBank Stadium", 30.3239, -81.6373, "outdoor"),
        _v("Nissan Stadium", 36.1665, -86.7713, "outdoor"),
        # --- AFC West ---
        _v("Empower Field at Mile High", 39.7439, -105.0201, "outdoor"),
        _v("GEHA Field at Arrowhead Stadium", 39.0489, -94.4839, "outdoor"),
        _v("Allegiant Stadium", 36.0909, -115.1833, "dome"),
        # SoFi has a fixed translucent canopy with open sides. Play is
        # unaffected by rain, so it counts as indoors here.
        _v("SoFi Stadium", 33.9535, -118.3392, "dome"),
        # --- NFC East ---
        _v("AT&T Stadium", 32.7473, -97.0945, "retractable"),
        _v("Lincoln Financial Field", 39.9008, -75.1675, "outdoor"),
        _v("Northwest Stadium", 38.9077, -76.8645, "outdoor"),
        # --- NFC North ---
        _v("Soldier Field", 41.8623, -87.6167, "outdoor"),
        _v("Ford Field", 42.3400, -83.0456, "dome"),
        _v("Lambeau Field", 44.5013, -88.0622, "outdoor"),
        _v("U.S. Bank Stadium", 44.9736, -93.2575, "dome"),
        # --- NFC South ---
        _v("Mercedes-Benz Stadium", 33.7554, -84.4008, "retractable"),
        _v("Bank of America Stadium", 35.2258, -80.8528, "outdoor"),
        _v("Caesars Superdome", 29.9511, -90.0812, "dome"),
        _v("Raymond James Stadium", 27.9759, -82.5033, "outdoor"),
        # --- NFC West ---
        _v("State Farm Stadium", 33.5276, -112.2626, "retractable"),
        _v("Levi's Stadium", 37.4033, -121.9694, "outdoor"),
        _v("Lumen Field", 47.5952, -122.3316, "outdoor"),
        # --- International ---
        _v("Wembley Stadium", 51.5560, -0.2795, "outdoor"),
        _v("Tottenham Hotspur Stadium", 51.6043, -0.0665, "outdoor"),
        _v("Allianz Arena", 48.2188, 11.6247, "outdoor"),
        _v("FC Bayern Munich Stadium", 48.2188, 11.6247, "outdoor"),
        _v("Deutsche Bank Park", 50.0686, 8.6455, "outdoor"),
        _v("Estadio Banorte", 19.3029, -99.1505, "outdoor"),
        _v("Estadio Azteca", 19.3029, -99.1505, "outdoor"),
        _v("Maracana Stadium", -22.9121, -43.2302, "outdoor"),
        _v("Neo Quimica Arena", -23.5453, -46.4742, "outdoor"),
        _v("Stade de France", 48.9245, 2.3601, "outdoor"),
        _v("Bernabeu", 40.4531, -3.6883, "retractable"),
        _v("Santiago Bernabeu", 40.4531, -3.6883, "retractable"),
        _v("Melbourne Cricket Ground", -37.8200, 144.9834, "outdoor"),
        _v("Croke Park", 53.3607, -6.2512, "outdoor"),
    ]
)


def _normalise(name: str) -> str:
    """Reduce a venue name to letters and digits only.

    Separators are dropped rather than collapsed to spaces, because feeds
    disagree in both directions: "U.S. Bank Stadium" against "US Bank Stadium",
    and "Mercedes-Benz Stadium" against "Mercedes Benz Stadium". Turning
    punctuation into a space fixes the first and breaks the second; removing it
    outright handles both.

    Losing word boundaries is safe here -- there are a few dozen venues and no
    pair collides.
    """
    return "".join(ch for ch in str(name).casefold() if ch.isalnum())


_BY_NORMALISED = {_normalise(name): venue for name, venue in VENUES.items()}


def lookup(stadium: str | None) -> Venue | None:
    """Find a venue by name. Returns None for anything unrecognised.

    Unlike the team mapping, an unknown venue is *not* fatal: the consequence
    is one game showing "weather unavailable" rather than a whole page failing
    over a stadium rename. Callers should warn so it gets added here.
    """
    if not stadium:
        return None
    return _BY_NORMALISED.get(_normalise(stadium))
