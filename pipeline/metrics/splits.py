"""Home and away splits: PPR points for skill players, accuracy for kickers.

Two tables, one question -- does this player travel? They share a module
because they share the join that answers it: a player-week has to be matched
against the schedule to learn which side was at home, and doing that twice in
two files is how the two tables would eventually disagree.

Both read a fixed trailing window rather than a season, which is the one place
these tables diverge from the rest of the page. Splitting by venue halves
whatever sample it is handed, and under the season rule weeks 2 to 7 hold one
to three games per venue -- a table of dashes. See `config.SPLIT_TRAILING_GAMES`
for the reasoning and `sample.trailing_describe` for the badge that says so.

No scoring rule is written here. nflverse computes `fantasy_points_ppr` and we
publish it: 1 per reception, 1 per 25 passing yards, four points for a passing
touchdown, a tenth per rushing and receiving yard. It also scores every kicker
0.0 -- its formula excludes kicking outright -- which is why the kicker table
carries accuracy and volume and no points column at all. Inventing one would
mean publishing our own scoring rule as though it were a fact.
"""

from __future__ import annotations

import logging
from typing import Final

import polars as pl

from pipeline import config
from pipeline.schema import KickerRow, SplitRow
from pipeline.sources import players as players_source
from pipeline.sources import schedule as schedule_source
from pipeline.sources import snaps as snaps_source
from pipeline.sources.team_map import UnmappedTeamError, to_abbr

log = logging.getLogger(__name__)

#: Positions the fantasy table will list. Everyone else on the field either
#: scores no PPR points or scores them by accident.
FANTASY_POSITIONS: Final[frozenset[str]] = frozenset({"QB", "RB", "WR", "TE"})

#: Pinned to the top of every team's table rather than ranked into it. On raw
#: PPR a starting quarterback outscores his own receivers on almost every team,
#: so ranking him would cost a skill-player row on all 32 without telling
#: anyone anything they did not already know.
PINNED_POSITION: Final[str] = "QB"

KICKER_POSITION: Final[str] = "K"


def home_by_game(seasons: list[int]) -> dict[str, str]:
    """game_id to the abbreviation of the side that was at home.

    Taken from the schedule rather than parsed out of the game_id. nflverse
    does encode it there -- "2026_01_NE_SEA" ends with the home team -- but
    that is a naming convention, and this is the feed that actually knows.
    """
    mapping: dict[str, str] = {}
    for season in seasons:
        for game in schedule_source.load_season(season):
            mapping[game.game_id] = game.home
    return mapping


def with_venue(weekly: pl.DataFrame, home_teams: dict[str, str]) -> pl.DataFrame:
    """Add `is_home`, dropping any row whose game is not in the schedule.

    Team codes go through `to_abbr` on both sides. nflverse is not internally
    consistent about Arizona, and a code that fails to match here would not
    raise -- it would quietly mark every one of that team's games an away game,
    which is a worse outcome than a missing table because it looks like data.
    """
    if weekly.height == 0:
        return weekly.with_columns(pl.lit(None, dtype=pl.Boolean).alias("is_home"))

    # Resolve each distinct code once rather than once per row.
    resolved: dict[str, str] = {}
    for code in weekly["team"].drop_nulls().unique().to_list():
        try:
            resolved[code] = to_abbr(code)
        except UnmappedTeamError:
            log.warning("Player stats carry team code %r, which does not map.", code)
            resolved[code] = code

    home_lookup = {game_id: to_abbr(team) for game_id, team in home_teams.items()}

    return (
        weekly.with_columns(
            pl.col("team").replace(resolved).alias("_team"),
            pl.col("game_id").replace_strict(home_lookup, default=None).alias("_home"),
        )
        .filter(pl.col("_home").is_not_null())
        .with_columns((pl.col("_team") == pl.col("_home")).alias("is_home"))
        .drop("_team", "_home")
    )


def trailing(
    weekly: pl.DataFrame, *, games: int = config.SPLIT_TRAILING_GAMES
) -> pl.DataFrame:
    """Each player's most recent N appearances, newest first.

    Counted per player rather than per team. A back who missed six weeks with a
    hamstring should be judged on the last seventeen games *he* played, not on
    the seventeen his team played without him.
    """
    return (
        weekly.sort(["player_id", "season", "week"], descending=[False, True, True])
        .with_columns(pl.int_range(pl.len()).over("player_id").alias("_appearance"))
        .filter(pl.col("_appearance") < games)
        .drop("_appearance")
    )


def _venue_aggregate(weekly: pl.DataFrame, value: str) -> pl.DataFrame:
    """Per player: the whole window, and each venue, in one pass."""
    home = pl.col("is_home").fill_null(False)

    return weekly.group_by("player_id").agg(
        player=pl.col("player_display_name").drop_nulls().last(),
        position=pl.col("position").drop_nulls().last(),
        games=pl.len(),
        total=pl.col(value).fill_null(0.0).sum(),
        home_games=home.sum(),
        home_total=pl.col(value).fill_null(0.0).filter(home).sum(),
        away_games=(~home).sum(),
        away_total=pl.col(value).fill_null(0.0).filter(~home).sum(),
    )


def _side_average(total, games: int, minimum: int) -> float | None:
    """A venue average, or None when too few games stand behind it.

    Below the floor one big afternoon moves the number by more than the split
    it is supposed to measure, so the cell shows a dash. The player keeps his
    row: his overall average is still a real number.
    """
    if games < minimum or games <= 0:
        return None
    return round(float(total or 0.0) / games, 1)


def fantasy_splits(
    weekly: pl.DataFrame,
    current_team: dict[str, str],
    *,
    limit: int = config.FANTASY_ROWS,
    min_side: int = config.SPLIT_MIN_GAMES_PER_SIDE,
) -> dict[str, list[SplitRow]]:
    """PPR at home and on the road, by team, quarterback first.

    A player who is not on a roster today is dropped outright, the way the
    passing and rushing tables drop him: he may have had a fine season, but he
    is not playing on Sunday.
    """
    frame = weekly.filter(pl.col("position").is_in(sorted(FANTASY_POSITIONS)))

    by_team: dict[str, list[dict]] = {}
    for row in _venue_aggregate(frame, "fantasy_points_ppr").iter_rows(named=True):
        team = current_team.get(row["player_id"])
        if team is None:
            continue

        games = int(row["games"] or 0)
        if games <= 0:
            continue

        home_games = int(row["home_games"] or 0)
        away_games = int(row["away_games"] or 0)
        home = _side_average(row["home_total"], home_games, min_side)
        away = _side_average(row["away_total"], away_games, min_side)

        by_team.setdefault(team, []).append(
            {
                "player": row["player"] or row["player_id"],
                "position": (row["position"] or "").upper() or None,
                "ppr_per_game": round(float(row["total"] or 0.0) / games, 1),
                "ppr_home": home,
                "ppr_away": away,
                "home_games": home_games,
                "away_games": away_games,
                # Home minus away, and absent when either side is. A split
                # measured against a dash is not a split.
                "ppr_split": (
                    round(home - away, 1)
                    if home is not None and away is not None
                    else None
                ),
                "_games": games,
            }
        )

    table: dict[str, list[SplitRow]] = {}
    for team, players in by_team.items():
        ordered = _pin_quarterback(players, limit)
        if ordered:
            table[team] = [
                SplitRow(**{k: v for k, v in player.items() if not k.startswith("_")})
                for player in ordered
            ]

    return table


def _pin_quarterback(players: list[dict], limit: int) -> list[dict]:
    """The busiest quarterback, then the highest scorers beneath him.

    If a team has no qualifying quarterback the row is simply absent and the
    table is one shorter. Inventing a starter out of a backup's two appearances
    would put a name on the page nobody expects to see take a snap.
    """
    quarterbacks = [p for p in players if p["position"] == PINNED_POSITION]
    others = sorted(
        (p for p in players if p["position"] != PINNED_POSITION), key=_scoring_order
    )

    if not quarterbacks:
        return others[:limit]

    # Appearances first, points second: the starter is the one who plays every
    # week, not the backup who threw two touchdowns in garbage time.
    quarterbacks.sort(
        key=lambda p: (-p["_games"], -(p["ppr_per_game"] or 0.0), p["player"])
    )
    return [quarterbacks[0], *others[: limit - 1]]


def _scoring_order(player: dict) -> tuple:
    """Highest PPR per game first, with the name breaking ties reproducibly."""
    return (-(player["ppr_per_game"] or 0.0), -player["_games"], player["player"])


def kicker_splits(
    weekly: pl.DataFrame,
    current_team: dict[str, str],
    *,
    min_att: int = config.KICKER_MIN_FG_ATT,
) -> dict[str, list[KickerRow]]:
    """Each team's kicker, at home and on the road.

    One kicker per team -- the one with the most appearances in the window --
    because a team carries one, and listing the man he replaced in October
    would read as a competition that is not happening.
    """
    frame = weekly.filter(pl.col("position") == KICKER_POSITION)

    per_venue = frame.group_by(["player_id", "is_home"]).agg(
        player=pl.col("player_display_name").drop_nulls().last(),
        games=pl.len(),
        fg_made=pl.col("fg_made").fill_null(0).sum(),
        fg_att=pl.col("fg_att").fill_null(0).sum(),
        fg_long=pl.col("fg_long").max(),
    )

    sides_by_player: dict[str, dict[bool, dict]] = {}
    appearances: dict[str, int] = {}
    names: dict[str, str] = {}
    for row in per_venue.iter_rows(named=True):
        if row["is_home"] is None:
            continue
        pid = row["player_id"]
        sides_by_player.setdefault(pid, {})[bool(row["is_home"])] = row
        appearances[pid] = appearances.get(pid, 0) + int(row["games"] or 0)
        names[pid] = row["player"] or pid

    # The busiest kicker per team, ties broken on the name so a rerun of the
    # same week produces the same page.
    primary: dict[str, str] = {}
    for pid, played in appearances.items():
        team = current_team.get(pid)
        if team is None:
            continue
        held = primary.get(team)
        if held is None or (played, names[pid]) > (appearances[held], names[held]):
            primary[team] = pid

    table: dict[str, list[KickerRow]] = {}
    for team, pid in primary.items():
        sides = sides_by_player.get(pid, {})
        attempts = sum(int(side["fg_att"] or 0) for side in sides.values())
        if attempts < min_att:
            continue

        rows = [
            _kicker_row(names[pid], sides[is_home], is_home)
            for is_home in (True, False)
            if is_home in sides
        ]
        if rows:
            table[team] = rows

    return table


def _kicker_row(player: str, side: dict, is_home: bool) -> KickerRow:
    """One venue's line. A rate with no denominator is None, never zero."""
    made = int(side["fg_made"] or 0)
    attempts = int(side["fg_att"] or 0)
    games = int(side["games"] or 0)

    return KickerRow(
        player=player,
        venue="home" if is_home else "away",
        fg_made=made,
        fg_att=attempts,
        # A fraction, like every other rate in the payload.
        fg_pct=round(made / attempts, 4) if attempts else None,
        fg_long=int(side["fg_long"]) if side["fg_long"] is not None else None,
        fg_att_per_game=round(attempts / games, 1) if games else None,
        games=games,
    )


def _window(stats_season: int) -> list[int]:
    """The seasons a trailing window may need to reach into.

    Two is always enough: seventeen games is one regular season, so the deepest
    a window ever reaches is the tail of the year before. In week 1
    `stats_season` is already last season, which is what keeps this from asking
    nflverse for a season that has not been played yet.
    """
    return [stats_season - 1, stats_season]


def _prepared(stats_season: int) -> pl.DataFrame:
    """The trailing window, venue-flagged, ready for either table."""
    seasons = _window(stats_season)
    weekly = players_source.trailing_weeks(seasons)
    return trailing(with_venue(weekly, home_by_game(seasons)))


def build_fantasy(stats_season: int, roster_season: int) -> dict[str, list[SplitRow]]:
    """Pull every feed the fantasy table needs and produce it."""
    return fantasy_splits(
        _prepared(stats_season), snaps_source.current_teams(roster_season)
    )


def build_kicking(stats_season: int, roster_season: int) -> dict[str, list[KickerRow]]:
    """Pull every feed the kicking table needs and produce it."""
    return kicker_splits(
        _prepared(stats_season), snaps_source.current_teams(roster_season)
    )
