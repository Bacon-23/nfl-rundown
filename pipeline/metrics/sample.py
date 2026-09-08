"""Which season a stat table describes, and how loudly to say so.

`config.stats_season()` decides which season's completed games a module reads.
This is its other half: what the reader is told about the sample they are
looking at. Both cutovers are single constants in `config.py`, so moving the
line where "too thin to publish" becomes "publishable" is one edit rather than
five.

The rule:

    week 1      prior season, full year      badge "2025 season"
    weeks 2-4   current season to date       badge "n = 3 games"
    week 5+     current season to date       no badge

Odds, weather, and injuries never carry a basis. They are always current.
"""

from __future__ import annotations

from pipeline import config
from pipeline.schema import SampleBasis


def basis_for(season: int, week: int, games_sampled: int | None = None) -> SampleBasis:
    """Which of the three sample regimes this week falls into."""
    if week <= config.PRIOR_SEASON_THROUGH_WEEK:
        return "prior_season"
    if week <= config.SMALL_SAMPLE_THROUGH_WEEK:
        return "small_sample"
    return "current_season"


def badge_for(season: int, week: int, games_sampled: int | None = None) -> str | None:
    """The badge text, or None when the sample speaks for itself.

    In the small-sample weeks the count is the point of the badge, so a module
    that could not work out how many games it saw says "early season" rather
    than inventing a number.
    """
    basis = basis_for(season, week)

    if basis == "prior_season":
        return f"{config.stats_season(season, week)} season"

    if basis == "small_sample":
        if games_sampled is None:
            return "early season"
        if games_sampled == 1:
            return "n = 1 game"
        return f"n = {games_sampled} games"

    return None


def describe(
    season: int, week: int, games_sampled: int | None = None
) -> tuple[SampleBasis, str | None]:
    """Basis and badge together, which is how every caller wants them."""
    return basis_for(season, week), badge_for(season, week, games_sampled)
