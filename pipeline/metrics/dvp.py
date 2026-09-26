"""Defense vs. position: what each defense gives up, and to whom.

For every defense and every role -- QB, WR, TE, RB -- this sums what that
defense's opponents did at the role and divides by the defense's games. "All
WRs" means every wide receiver who played against it, combined. Each figure
is then ranked 1 to 32, where 1 always favours the offense: the most yards
allowed, but the *fewest* interceptions.

Beside that sits each offense's own players, with their per-game lines. The
renderer colours a player's cell by the rank of the defense he is about to
face at his role, which is the whole point of the tab: a matchup read, not two
unrelated tables.

Box-score columns come from the weekly player stats, which carry the
opponent. Red zone looks and the longest gain come from play-by-play, joined
back onto the same player-games so both halves agree on who played whom.

The window is the season rule every other season module follows, so in week 1
this reads last season and the module is badged accordingly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final

import polars as pl

from pipeline import config
from pipeline.schema import DVP_SECTIONS, DvpCell, DvpPlayerRow, DvpRoleRow, DvpSection, DvpSide
from pipeline.sources import pbp as pbp_source
from pipeline.sources import players as players_source
from pipeline.sources import snaps as snaps_source
from pipeline.sources.team_map import UnmappedTeamError, to_abbr

log = logging.getLogger(__name__)

#: A fullback is a running back here, as it is on every DvP table: nobody
#: ranks defenses against fullbacks, and his touches are backfield touches.
ROLE_OF_POSITION: Final[dict[str, str]] = {
    "QB": "QB",
    "WR": "WR",
    "TE": "TE",
    "RB": "RB",
    "FB": "RB",
}

ROLES: Final[tuple[str, ...]] = ("QB", "WR", "TE", "RB")

#: Stat key to the weekly column it sums. `tgt` is not ranked -- no section
#: lists it -- but the receiving player rows show it and sort on it.
BOX_SCORE: Final[dict[str, str]] = {
    "pass_yds": "passing_yards",
    "comp": "completions",
    "att": "attempts",
    "pass_td": "passing_tds",
    "int": "passing_interceptions",
    "rec": "receptions",
    "tgt": "targets",
    "rec_yds": "receiving_yards",
    "rec_td": "receiving_tds",
    "carries": "carries",
    "rush_yds": "rushing_yards",
    "rush_td": "rushing_tds",
    "ppr": "fantasy_points_ppr",
}

#: Counted from play-by-play and summed per game.
PLAY_COUNTS: Final[tuple[str, ...]] = ("rz_tgt", "rz_car")

#: The longest gain in a game. Averaged over games rather than taken as a
#: season maximum: that is the number a Longest Reception prop prices, and a
#: season maximum describes one play.
LONGS: Final[tuple[str, ...]] = ("long_rec", "long_rush")

STATS: Final[tuple[str, ...]] = (*BOX_SCORE, *PLAY_COUNTS, *LONGS)

#: Ranked ascending, so that rank 1 still favours the offense.
LOWER_IS_BETTER: Final[frozenset[str]] = frozenset({"int"})


@dataclass(frozen=True)
class DvpTables:
    """Both halves, keyed by team: what each defense allows, and each offense's
    players. A game pairs one of each."""

    allows: dict[str, dict[str, list[DvpRoleRow]]] = field(default_factory=dict)
    players: dict[str, dict[str, list[DvpPlayerRow]]] = field(default_factory=dict)


def player_games(weekly: pl.DataFrame, plays: pl.DataFrame) -> pl.DataFrame:
    """One row per player per game: his role, both teams, and every stat.

    Only the four roles survive. Team codes go through `to_abbr`, because the
    alternative -- a code that fails to match -- does not raise; it quietly
    splits one defense into two half-defenses.
    """
    pbp_source.check_columns(weekly, players_source.DVP_COLUMNS, "Player stats (DvP)")
    pbp_source.check_columns(plays, pbp_source.DVP_COLUMNS, "Play-by-play (DvP)")

    codes = {
        code: _abbr(code)
        for column in ("team", "opponent_team")
        for code in weekly[column].drop_nulls().unique().to_list()
    }

    box = (
        weekly.filter(
            pl.col("position").is_in(sorted(ROLE_OF_POSITION))
            & pl.col("opponent_team").is_not_null()
            & pl.col("game_id").is_not_null()
        )
        .with_columns(
            pl.col("position").replace_strict(ROLE_OF_POSITION).alias("role"),
            pl.col("team").replace(codes).alias("team"),
            pl.col("opponent_team").replace(codes).alias("defense"),
        )
        # Summed rather than taken as-is, so a feed that ever splits a
        # player-game in two cannot count one game twice.
        .group_by(["player_id", "game_id"])
        .agg(
            player=pl.col("player_display_name").drop_nulls().last(),
            position=pl.col("position").drop_nulls().last(),
            role=pl.col("role").last(),
            team=pl.col("team").last(),
            defense=pl.col("defense").last(),
            **{
                key: pl.col(column).fill_null(0).sum().cast(pl.Float64)
                for key, column in BOX_SCORE.items()
            },
        )
    )

    return box.join(_play_level(plays), on=["player_id", "game_id"], how="left").with_columns(
        pl.col(key).fill_null(0.0) for key in (*PLAY_COUNTS, *LONGS)
    )


def _play_level(plays: pl.DataFrame) -> pl.DataFrame:
    """Red zone looks and longest gains, per player per game.

    A red zone target follows `pbp.scoring_usage`: a pass with a named
    receiver, no sack, no two-point try. A red zone carry is a designed run,
    as the Inside 5 column counts it -- scrambles and kneels are out, a sneak
    is in. The longest rush is different on purpose: the box score counts a
    scramble as a carry, so the long has to as well.
    """
    live = plays.filter(
        (pl.col("season_type") == pbp_source.REGULAR_SEASON)
        & (pl.col("two_point_attempt").fill_null(0) == 0)
    )
    in_red_zone = (pl.col("yardline_100") <= pbp_source.RED_ZONE_YARDS).fill_null(False)

    receiving = (
        live.filter(
            (pl.col("play_type") == "pass")
            & pl.col("receiver_player_id").is_not_null()
            & (pl.col("sack").fill_null(0) == 0)
        )
        .group_by(["receiver_player_id", "game_id"])
        .agg(
            rz_tgt=in_red_zone.sum().cast(pl.Float64),
            long_rec=pl.col("yards_gained")
            .filter(pl.col("complete_pass").fill_null(0) == 1)
            .max()
            .cast(pl.Float64),
        )
        .rename({"receiver_player_id": "player_id"})
    )

    runs = live.filter(
        (pl.col("play_type") == "run")
        & pl.col("rusher_player_id").is_not_null()
        & (pl.col("qb_kneel").fill_null(0) == 0)
    )
    rushing = (
        runs.group_by(["rusher_player_id", "game_id"])
        .agg(
            rz_car=(in_red_zone & (pl.col("qb_scramble").fill_null(0) == 0)).sum().cast(pl.Float64),
            long_rush=pl.col("yards_gained").max().cast(pl.Float64),
        )
        .rename({"rusher_player_id": "player_id"})
    )

    return receiving.join(rushing, on=["player_id", "game_id"], how="full", coalesce=True)


def allowed(games: pl.DataFrame) -> dict[str, dict[str, dict[str, float]]]:
    """Per defense, per role, per stat: allowed per game.

    The denominator is the *defense's* games, so a game in which it faced no
    tight end still counts -- it allowed nothing to the role that day. And
    every role is filled for every defense, zero where it faced nobody,
    because every defense gets ranked on every row.

    A long is the most any player at the role gained against it in a game,
    averaged across its games.
    """
    defense_games = {
        row["defense"]: int(row["games"])
        for row in games.group_by("defense")
        .agg(games=pl.col("game_id").n_unique())
        .iter_rows(named=True)
    }

    per_game = games.group_by(["defense", "role", "game_id"]).agg(
        *(pl.col(key).sum() for key in (*BOX_SCORE, *PLAY_COUNTS)),
        *(pl.col(key).max() for key in LONGS),
    )
    totals = per_game.group_by(["defense", "role"]).agg(pl.col(key).sum() for key in STATS)

    result = {
        defense: {role: dict.fromkeys(STATS, 0.0) for role in ROLES} for defense in defense_games
    }
    for row in totals.iter_rows(named=True):
        played = defense_games[row["defense"]]
        result[row["defense"]][row["role"]] = {
            key: float(row[key] or 0.0) / played for key in STATS
        }

    return result


def ranks(table: dict[str, dict[str, dict[str, float]]]) -> dict[str, dict[str, dict[str, int]]]:
    """1 to 32 per role and stat, where 1 favours the offense.

    Ties break on the abbreviation, the same rule PROE and EPA follow, so a
    rerun of the same week never swaps two defenses.
    """
    result: dict[str, dict[str, dict[str, int]]] = {
        defense: {role: {} for role in ROLES} for defense in table
    }

    for role in ROLES:
        for key in STATS:
            sign = 1.0 if key in LOWER_IS_BETTER else -1.0
            ordered = sorted(table, key=lambda d: (sign * table[d][role][key], d))
            for position, defense in enumerate(ordered, start=1):
                result[defense][role][key] = position

    return result


def allows_rows(
    table: dict[str, dict[str, dict[str, float]]],
) -> dict[str, dict[str, list[DvpRoleRow]]]:
    """Each defense's rows, section by section, in `DVP_SECTIONS` order.

    Ranked on the unrounded values; only the published figure is rounded.
    """
    ranked = ranks(table)

    return {
        defense: {
            section: [
                DvpRoleRow(
                    role=role,
                    stats={
                        key: DvpCell(
                            value=round(table[defense][role][key], 1),
                            rank=ranked[defense][role][key],
                        )
                        for key in keys
                    },
                )
                for role in roles
            ]
            for section, (roles, keys) in DVP_SECTIONS.items()
        }
        for defense in table
    }


def player_lines(
    games: pl.DataFrame,
    current_team: dict[str, str],
    *,
    receiving_rows: int = config.DVP_RECEIVING_ROWS,
    rushing_rows: int = config.DVP_RUSHING_ROWS,
    min_carries: float = config.RUSHER_MIN_ATT_PER_GAME,
) -> dict[str, dict[str, list[DvpPlayerRow]]]:
    """Each offense's players, with their own per-game lines.

    Listed under the team a player is on *now*, and only if he is on an
    active roster -- the same rule as every other player table. His line is
    his, wherever he earned it; the module's badge says which season.

    - Passing: the starting quarterback, meaning the one who played most.
    - Receiving: pass catchers by targets per game.
    - Rushing: that quarterback, then backs above the workload floor.
    """
    per_player = games.group_by("player_id").agg(
        player=pl.col("player").drop_nulls().last(),
        position=pl.col("position").drop_nulls().last(),
        role=pl.col("role").last(),
        games=pl.col("game_id").n_unique(),
        **{key: pl.col(key).mean() for key in STATS},
    )

    by_team: dict[str, list[dict]] = {}
    for row in per_player.iter_rows(named=True):
        team = current_team.get(row["player_id"])
        if team is not None:
            by_team.setdefault(team, []).append(row)

    table: dict[str, dict[str, list[DvpPlayerRow]]] = {}
    for team, players in by_team.items():
        quarterbacks = sorted(
            (p for p in players if p["role"] == "QB"),
            key=lambda p: (-p["games"], -p["ppr"], p["player"]),
        )
        starter = quarterbacks[:1]

        catchers = sorted(
            (p for p in players if p["role"] != "QB" and p["tgt"] > 0),
            key=lambda p: (-p["tgt"], p["player"]),
        )[:receiving_rows]

        backs = sorted(
            (p for p in players if p["role"] == "RB" and p["carries"] >= min_carries),
            key=lambda p: (-p["carries"], p["player"]),
        )

        sections = {
            "passing": starter,
            "receiving": catchers,
            "rushing": (starter + backs)[:rushing_rows],
        }
        table[team] = {
            section: [_player_row(p, section) for p in chosen]
            for section, chosen in sections.items()
            if chosen
        }

    return table


def _player_row(player: dict, section: str) -> DvpPlayerRow:
    _, keys = DVP_SECTIONS[section]
    if section == "receiving":
        keys = ("tgt", *keys)

    return DvpPlayerRow(
        player=player["player"] or player["player_id"],
        role=player["role"],
        position=(player["position"] or "").upper() or None,
        games=int(player["games"]),
        stats={key: round(float(player[key]), 1) for key in keys},
    )


def side(tables: DvpTables, *, offense: str, defense: str) -> DvpSide | None:
    """One offense against one defense, or None when neither half exists."""
    sections = {}
    for section in DVP_SECTIONS:
        allows = tables.allows.get(defense, {}).get(section, [])
        players = tables.players.get(offense, {}).get(section, [])
        if allows or players:
            sections[section] = DvpSection(allows=allows, players=players)

    return DvpSide(**sections) if sections else None


def build(stats_season: int, roster_season: int) -> DvpTables:
    """Pull every feed the DvP tab needs and produce both halves."""
    games = player_games(players_source.load(stats_season), pbp_source.load(stats_season))
    return DvpTables(
        allows=allows_rows(allowed(games)),
        players=player_lines(games, snaps_source.current_teams(roster_season)),
    )


def _abbr(code: str) -> str:
    try:
        return to_abbr(code)
    except UnmappedTeamError:
        log.warning("Player stats carry team code %r, which does not map.", code)
        return code
