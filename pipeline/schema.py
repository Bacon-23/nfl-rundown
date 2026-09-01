"""The payload contract between the pipeline and WordPress.

This module is the single definition of what a week looks like on the wire.
The PHP side reads these keys by dot-path, so renaming a field here is a
breaking change that needs a matching edit in `wordpress/trinity-rundown`.

Optional fields are genuinely optional: the renderer shows a dash when a value
is missing, which is always better than blocking a whole week's publish over
one absent number.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OddsSource = Literal["odds_api", "nflverse_fallback"]
SampleBasis = Literal["prior_season", "small_sample", "current_season"]


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Team(Base):
    abbr: str
    name: str
    color: str | None = None
    logo: str | None = None
    record: str | None = None
    ats_record: str | None = None
    ou_record: str | None = None
    moneyline: int | None = None


class Odds(Base):
    source: OddsSource
    book: str | None = None
    book_label: str | None = None

    #: Always negative, read from the favorite's side: -4.5 means the team in
    #: `spread_favorite` is laying 4.5. Zero means a pick'em.
    spread: float | None = None
    spread_favorite: str | None = None
    spread_price_home: int | None = None
    spread_price_away: int | None = None

    total: float | None = None
    over_price: int | None = None
    under_price: int | None = None

    home_team_total: float | None = None
    away_team_total: float | None = None

    #: True when team totals were computed from spread and total rather than
    #: taken from a posted market. Surfaced in the admin screen.
    team_totals_derived: bool = False

    #: Populated by WordPress from the write-once opening_line column, not by
    #: the pipeline. Declared here so the contract is documented in one place.
    opening: dict | None = None


class Kickoff(Base):
    utc: datetime
    display: str
    network: str | None = None


class Weather(Base):
    summary: str
    temp_f: int | None = None
    wind_mph: int | None = None
    precip_chance: int | None = None
    is_indoors: bool = False


class InjuryRow(Base):
    team: str
    player: str
    position: str | None = None
    status: str
    #: Short injury descriptor, e.g. "Knee" or "Knee - ACL".
    note: str | None = None
    #: Practice participation (DNP/LP/FP) when the nflverse feed supplies it.
    practice: str | None = None
    #: Beat-writer note from ESPN. Shown to the editor; too long for the table.
    comment: str | None = None


class TeamEfficiency(Base):
    team: str
    pass_rate: float | None = None
    rush_rate: float | None = None
    proe: float | None = None
    pace: float | None = None
    plays_per_game: float | None = None
    epa_per_play: float | None = None
    epa_rank: int | None = None


class ReceiverRow(Base):
    player: str
    role: str | None = None
    target_share: float | None = None
    #: Targets per estimated pass snap. A proxy for TPRR, which needs charted
    #: route data we do not license. Never label this "TPRR" in the UI.
    target_rate: float | None = None
    rec_yds_per_game: float | None = None


class RusherRow(Base):
    player: str
    snap_share: float | None = None
    rush_att_per_game: float | None = None
    target_share: float | None = None
    yards_per_att: float | None = None


class Module(Base):
    """A stat table plus the provenance a reader needs to weigh it."""

    basis: SampleBasis
    #: Rendered as a badge, e.g. "2025 season" or "n = 3 games".
    badge: str | None = None
    games_sampled: int | None = None


class EfficiencyModule(Module):
    rows: list[TeamEfficiency] = Field(default_factory=list)


class PassingModule(Module):
    away: list[ReceiverRow] = Field(default_factory=list)
    home: list[ReceiverRow] = Field(default_factory=list)


class RushingModule(Module):
    away: list[RusherRow] = Field(default_factory=list)
    home: list[RusherRow] = Field(default_factory=list)


class Game(Base):
    game_id: str
    season: int
    week: int
    sort_order: int = 0

    away: Team
    home: Team
    kickoff: Kickoff
    odds: Odds

    weather: Weather | None = None

    #: None means "could not be determined this run" -- which is NOT the same
    #: as an empty list, meaning "nobody is reported hurt". WordPress keeps the
    #: previously stored table when this key is absent, so a transient ESPN
    #: outage cannot blank out a good injury report.
    injuries: list[InjuryRow] | None = None
    efficiency: EfficiencyModule | None = None
    passing: PassingModule | None = None
    rushing: RushingModule | None = None

    #: Reserved so adding defense-vs-position in Phase 5 does not change the
    #: shape of anything already shipped.
    dvp: dict | None = None


class WeekPayload(Base):
    season: int
    week: int
    generated_at: datetime
    games: list[Game]

    def wire(self) -> dict:
        """Serialise for the REST body, dropping nulls to keep it compact."""
        return self.model_dump(mode="json", exclude_none=True)
