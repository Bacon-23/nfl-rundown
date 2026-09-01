"""Against-the-spread and over/under season records.

Both are computed from completed games in the nflverse schedule, which already
carries the closing line and the final score. No extra feed is involved.

The convention worth stating once: nflverse's `spread_line` is home-relative
and positive when the home team is favored, so the home side covers when
`home_score - away_score` exceeds it. A result landing exactly on the number is
a push for both teams -- counted in its own column, never as a win.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from pipeline.sources import schedule as schedule_source
from pipeline.sources.schedule import ScheduledGame

#: Weeks 1-18 are the regular season; 19 and up are the playoffs. A record
#: badged "2025 season" means the regular season, which is what every other
#: site publishes -- a deep playoff run should not quietly inflate it.
REGULAR_SEASON_WEEKS = 18


@dataclass(frozen=True)
class Record:
    """A won-lost-pushed tally.

    For over/under, `won` counts overs and `lost` counts unders, which is how
    such records are conventionally printed.
    """

    won: int = 0
    lost: int = 0
    pushed: int = 0

    @property
    def played(self) -> int:
        return self.won + self.lost + self.pushed

    def __str__(self) -> str:
        """Render as "10-6", or "9-7-1" when any game pushed."""
        if not self.played:
            return ""
        if not self.pushed:
            return f"{self.won}-{self.lost}"
        return f"{self.won}-{self.lost}-{self.pushed}"


@dataclass
class TeamRecords:
    ats: Record = field(default_factory=Record)
    ou: Record = field(default_factory=Record)


def season_records(games: Iterable[ScheduledGame]) -> dict[str, TeamRecords]:
    """Tally ATS and over/under records per team over completed regular-season games.

    A team with nothing to report is absent from the result rather than
    present with an empty record, so the renderer can show a dash instead of a
    misleading "0-0".
    """
    tallies: dict[str, _Tally] = {}

    for game in games:
        if not game.is_final or game.week > REGULAR_SEASON_WEEKS:
            continue

        margin = game.home_score - game.away_score
        combined = game.home_score + game.away_score

        # The two lines are independent: a game missing one still counts
        # toward the other.
        if game.spread_line is not None:
            if margin == game.spread_line:
                tallies.setdefault(game.home, _Tally()).ats_pushed += 1
                tallies.setdefault(game.away, _Tally()).ats_pushed += 1
            else:
                covered, missed = (
                    (game.home, game.away)
                    if margin > game.spread_line
                    else (game.away, game.home)
                )
                tallies.setdefault(covered, _Tally()).ats_won += 1
                tallies.setdefault(missed, _Tally()).ats_lost += 1

        if game.total_line is not None:
            for team in (game.home, game.away):
                tally = tallies.setdefault(team, _Tally())
                if combined == game.total_line:
                    tally.ou_pushed += 1
                elif combined > game.total_line:
                    tally.ou_won += 1
                else:
                    tally.ou_lost += 1

    return {team: tally.freeze() for team, tally in tallies.items()}


def load_records(season: int) -> dict[str, TeamRecords]:
    """Season records straight from nflverse, for callers without a game list."""
    return season_records(schedule_source.load_season(season))


@dataclass
class _Tally:
    """Mutable accumulator, frozen into a `TeamRecords` at the end."""

    ats_won: int = 0
    ats_lost: int = 0
    ats_pushed: int = 0
    ou_won: int = 0
    ou_lost: int = 0
    ou_pushed: int = 0

    def freeze(self) -> TeamRecords:
        return TeamRecords(
            ats=Record(self.ats_won, self.ats_lost, self.ats_pushed),
            ou=Record(self.ou_won, self.ou_lost, self.ou_pushed),
        )
