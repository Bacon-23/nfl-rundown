"""Quarterbacks: each offense's starter, and the defense he is about to face.

Everything is counted per dropback, from play-by-play. A dropback is
nflfastR's `qb_dropback`: a pass attempt, a sack or a scramble. Two-point
tries are left out, as the box score leaves them out. A scramble has no
`passer_player_id` -- the quarterback is in `rusher_player_id` -- so the
dropback's quarterback is whichever of the two is set.

The same arithmetic runs twice: once grouped by the quarterback, once by the
defense on the field, so "sack rate" means one thing on both rows of a side.
Pressure and blitz come from charting feeds (see `sources/charting.py`), and
each counts only the dropbacks its feed has charted, since both run a few days
behind play-by-play.

Defense ranks run 1 to 32 with 1 favouring the offense, as on the DvP tab: the
most yards allowed, but the *lowest* sack and pressure rates. Scramble rate,
aDOT and blitz rate have no direction to favour -- a blitz is not good or bad
for the offense on its own -- so they rank by frequency, most first, and the
renderer leaves them untinted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final

import polars as pl

from pipeline.schema import QB_STATS, DvpCell, QbDefenseRow, QbRow, QbSide, QbSplitRow
from pipeline.sources import charting as charting_source
from pipeline.sources import pbp as pbp_source
from pipeline.sources import snaps as snaps_source
from pipeline.sources.team_map import UnmappedTeamError, to_abbr

log = logging.getLogger(__name__)

#: Ranked ascending, so that rank 1 still favours the offense.
LOWER_FAVOURS_OFFENSE: Final[frozenset[str]] = frozenset({"sack_rate", "pressure_rate"})

#: Ranked by frequency with no band: neither end favours the offense.
NEUTRAL: Final[frozenset[str]] = frozenset({"scramble_rate", "adot", "blitz_rate"})

#: One extra rusher makes a blitz. FTN counts blitzers beyond the standard
#: rush, so 0 is a four-man (or lighter) rush.
MIN_BLITZERS: Final[int] = 1

#: Decimal places each stat is published to. Ranks use the unrounded values.
PLACES: Final[dict[str, int]] = {
    "att": 1,
    "dropbacks": 1,
    "sack_rate": 4,
    "scramble_rate": 4,
    "adot": 2,
    "cpoe": 2,
    "cmp_pct": 4,
    "ypa": 2,
    "pressure_rate": 4,
    "blitz_rate": 4,
}


@dataclass(frozen=True)
class QbTables:
    """Each team's starter and each defense's row, keyed by team. A game
    pairs one of each. `warnings` names a charting feed that failed, which
    costs its column and nothing else."""

    quarterbacks: dict[str, QbRow] = field(default_factory=dict)
    splits: dict[str, list[QbSplitRow]] = field(default_factory=dict)
    defenses: dict[str, QbDefenseRow] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def dropbacks(plays: pl.DataFrame) -> pl.DataFrame:
    """One row per regular-season dropback, with its outcome flags.

    `blitzed` starts null -- unknown -- and `with_blitzes` fills it where FTN
    has charted the play.
    """
    pbp_source.check_columns(plays, pbp_source.QB_COLUMNS, "Play-by-play (quarterbacks)")

    attempt = (
        (pl.col("play_type") == "pass")
        & (pl.col("sack").fill_null(0) == 0)
        & (pl.col("qb_scramble").fill_null(0) == 0)
    )

    return plays.filter(
        (pl.col("season_type") == pbp_source.REGULAR_SEASON)
        & (pl.col("qb_dropback").fill_null(0) == 1)
        & pl.col("play_type").is_in(["pass", "run"])
        & (pl.col("two_point_attempt").fill_null(0) == 0)
        & pl.col("posteam").is_not_null()
        & pl.col("defteam").is_not_null()
    ).select(
        "game_id",
        pl.col("play_id").cast(pl.Int64),
        "posteam",
        "defteam",
        pl.coalesce("passer_player_id", "rusher_player_id").alias("qb_id"),
        pl.coalesce("passer_player_name", "rusher_player_name").alias("qb_name"),
        attempt.alias("attempt"),
        (pl.col("sack").fill_null(0) == 1).alias("sack"),
        (pl.col("qb_scramble").fill_null(0) == 1).alias("scramble"),
        (attempt & (pl.col("complete_pass").fill_null(0) == 1)).alias("complete"),
        # Yards per attempt counts only attempts; a sack's lost yards are the
        # sack rate's business.
        pl.when(attempt).then(pl.col("passing_yards").fill_null(0.0)).alias("pass_yards"),
        pl.when(attempt).then(pl.col("air_yards")).alias("air_yards"),
        pl.when(attempt).then(pl.col("cpoe")).alias("cpoe"),
        pl.lit(None, dtype=pl.Boolean).alias("blitzed"),
    )


def with_blitzes(frame: pl.DataFrame, blitzes: pl.DataFrame) -> pl.DataFrame:
    """Mark each dropback FTN charted as blitzed or not. Uncharted stays null."""
    return (
        frame.drop("blitzed")
        .join(blitzes, on=["game_id", "play_id"], how="left")
        .with_columns((pl.col("n_blitzers") >= MIN_BLITZERS).alias("blitzed"))
        .drop("n_blitzers")
    )


def _line(frame: pl.DataFrame, by: str) -> pl.DataFrame:
    """Every stat but pressure, grouped by `by`. Rates stay unrounded."""
    return (
        frame.group_by(by)
        .agg(
            games=pl.col("game_id").n_unique(),
            n_dropbacks=pl.len(),
            n_attempts=pl.col("attempt").sum(),
            sack_rate=pl.col("sack").mean(),
            scramble_rate=pl.col("scramble").mean(),
            adot=pl.col("air_yards").mean(),
            cpoe=pl.col("cpoe").mean(),
            completions=pl.col("complete").sum(),
            pass_yards=pl.col("pass_yards").sum(),
            # Of the dropbacks FTN has charted. Null when it has charted none.
            blitz_rate=pl.col("blitzed").mean(),
        )
        .with_columns(
            att=pl.col("n_attempts") / pl.col("games"),
            dropbacks=pl.col("n_dropbacks") / pl.col("games"),
            cmp_pct=pl.when(pl.col("n_attempts") > 0).then(
                pl.col("completions") / pl.col("n_attempts")
            ),
            ypa=pl.when(pl.col("n_attempts") > 0).then(pl.col("pass_yards") / pl.col("n_attempts")),
        )
    )


def pressure_rates(
    frame: pl.DataFrame, pressures: pl.DataFrame, key: pl.DataFrame
) -> tuple[dict[str, float], dict[str, float]]:
    """Pressure rate per quarterback and per defense.

    PFR counts pressures per passer per game, so the denominator is the
    play-by-play dropbacks from the games PFR has charted -- for a
    quarterback, his own; for a defense, every dropback against it. `key` maps
    PFR ids to gsis ids; a passer it cannot map still counts for the defense.
    """
    pressures = pressures.with_columns(pl.col("opponent").map_elements(_abbr, return_dtype=pl.Utf8))

    by_defense = pressures.group_by(["game_id", "opponent"]).agg(
        pressured=pl.col("times_pressured").sum()
    )
    faced = (
        frame.group_by(["game_id", "defteam"])
        .agg(n=pl.len())
        .join(
            by_defense,
            left_on=["game_id", "defteam"],
            right_on=["game_id", "opponent"],
            how="inner",
        )
        .group_by("defteam")
        .agg(pl.col("pressured").sum(), pl.col("n").sum())
    )

    by_passer = (
        pressures.join(key, left_on="pfr_player_id", right_on="pfr_id", how="inner")
        .group_by(["game_id", "player_id"])
        .agg(pressured=pl.col("times_pressured").sum())
    )
    felt = (
        frame.filter(pl.col("qb_id").is_not_null())
        .group_by(["game_id", "qb_id"])
        .agg(n=pl.len())
        .join(
            by_passer, left_on=["game_id", "qb_id"], right_on=["game_id", "player_id"], how="inner"
        )
        .group_by("qb_id")
        .agg(pl.col("pressured").sum(), pl.col("n").sum())
    )

    return (
        {r["qb_id"]: r["pressured"] / r["n"] for r in felt.iter_rows(named=True) if r["n"]},
        {r["defteam"]: r["pressured"] / r["n"] for r in faced.iter_rows(named=True) if r["n"]},
    )


def ranks(table: dict[str, dict[str, float | None]]) -> dict[str, dict[str, int | None]]:
    """1 to 32 per stat, where 1 favours the offense.

    A defense with no value -- no charted games -- gets no rank, and the rest
    are ranked among themselves. Ties break on the abbreviation, so a rerun of
    the same week never swaps two defenses.
    """
    result: dict[str, dict[str, int | None]] = {team: {} for team in table}

    for key in QB_STATS:
        sign = 1.0 if key in LOWER_FAVOURS_OFFENSE else -1.0
        known = [team for team in table if table[team].get(key) is not None]
        ordered = sorted(known, key=lambda t: (sign * table[t][key], t))
        for team in table:
            result[team][key] = None
        for position, team in enumerate(ordered, start=1):
            result[team][key] = position

    return result


def _stats(row: dict, pressure: float | None) -> dict[str, float | None]:
    values = {key: row.get(key) for key in QB_STATS}
    values["pressure_rate"] = pressure
    return values


def _rounded(stats: dict[str, float | None]) -> dict[str, float | None]:
    return {
        key: None if value is None else round(float(value), PLACES[key])
        for key, value in stats.items()
    }


def starters(frame: pl.DataFrame, current_team: dict[str, str]) -> dict[str, str]:
    """Each team's starter, by gsis id: the quarterback on its roster today
    with the most dropbacks in the window. Ties break on the id, so a rerun
    never swaps them."""
    counts = frame.filter(pl.col("qb_id").is_not_null()).group_by("qb_id").agg(n=pl.len())

    best: dict[str, tuple[int, str]] = {}
    for row in counts.iter_rows(named=True):
        team = current_team.get(row["qb_id"])
        if team is not None and (row["n"], row["qb_id"]) > best.get(team, (-1, "")):
            best[team] = (row["n"], row["qb_id"])

    return {team: qb_id for team, (_, qb_id) in best.items()}


def quarterback_rows(
    frame: pl.DataFrame, pressure: dict[str, float], chosen: dict[str, str]
) -> dict[str, QbRow]:
    """Each starter's line. It is his, wherever he earned it; the module's
    badge says which season."""
    wanted = set(chosen.values())
    lines = {
        row["qb_id"]: row
        for row in _line(frame.filter(pl.col("qb_id").is_in(wanted)), "qb_id")
        .join(frame.group_by("qb_id").agg(pl.col("qb_name").drop_nulls().last()), on="qb_id")
        .iter_rows(named=True)
    }

    return {
        team: QbRow(
            player=lines[qb_id]["qb_name"] or qb_id,
            games=int(lines[qb_id]["games"]),
            stats=_rounded(_stats(lines[qb_id], pressure.get(qb_id))),
        )
        for team, qb_id in chosen.items()
    }


def split_rows(frame: pl.DataFrame, qb_id: str) -> list[QbSplitRow]:
    """His dropbacks against the blitz and without one. Empty when FTN has
    charted none of them."""
    charted = frame.filter((pl.col("qb_id") == qb_id) & pl.col("blitzed").is_not_null())
    rows = []
    for split, blitzed in (("blitz", True), ("no_blitz", False)):
        plays = charted.filter(pl.col("blitzed") == blitzed)
        attempts = int(plays["attempt"].sum()) if plays.height else 0
        rows.append(
            QbSplitRow(
                split=split,
                dropbacks=plays.height,
                cmp_pct=round(int(plays["complete"].sum()) / attempts, 4) if attempts else None,
                ypa=round(float(plays["pass_yards"].sum()) / attempts, 2) if attempts else None,
                sack_rate=round(int(plays["sack"].sum()) / plays.height, 4)
                if plays.height
                else None,
            )
        )
    return rows if charted.height else []


def defense_rows(frame: pl.DataFrame, pressure: dict[str, float]) -> dict[str, QbDefenseRow]:
    """Every defense's row, ranked on the unrounded values."""
    lines = {row["defteam"]: row for row in _line(frame, "defteam").iter_rows(named=True)}
    table = {team: _stats(row, pressure.get(team)) for team, row in lines.items()}
    ranked = ranks(table)

    return {
        team: QbDefenseRow(
            team=team,
            games=int(lines[team]["games"]),
            stats={
                key: DvpCell(value=_rounded({key: value})[key], rank=ranked[team][key])
                for key, value in table[team].items()
            },
        )
        for team in table
    }


def side(tables: QbTables, *, offense: str, defense: str) -> QbSide | None:
    """One offense's quarterback against one defense, or None when neither
    half exists."""
    quarterback = tables.quarterbacks.get(offense)
    against = tables.defenses.get(defense)
    if quarterback is None and against is None:
        return None
    return QbSide(
        quarterback=quarterback,
        defense=against,
        splits=tables.splits.get(offense, []),
    )


def build(stats_season: int, roster_season: int) -> QbTables:
    """Pull every feed the tab needs. A charting feed that fails costs its
    column and is reported; play-by-play failing costs the tab."""
    frame = dropbacks(pbp_source.load(stats_season))
    warnings: list[str] = []

    try:
        frame = with_blitzes(frame, charting_source.blitzes(stats_season))
    except Exception as exc:  # noqa: BLE001 - one column must not sink the tab
        warnings.append(f"Blitz rates unavailable ({stats_season}): {exc}")

    qb_pressure: dict[str, float] = {}
    def_pressure: dict[str, float] = {}
    try:
        qb_pressure, def_pressure = pressure_rates(
            frame,
            charting_source.pressures(stats_season),
            snaps_source.player_key(stats_season),
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Pressure rates unavailable ({stats_season}): {exc}")

    chosen = starters(frame, snaps_source.current_teams(roster_season))

    return QbTables(
        quarterbacks=quarterback_rows(frame, qb_pressure, chosen),
        splits={team: split_rows(frame, qb_id) for team, qb_id in chosen.items()},
        defenses=defense_rows(frame, def_pressure),
        warnings=warnings,
    )


def _abbr(code: str) -> str:
    try:
        return to_abbr(code)
    except UnmappedTeamError:
        log.warning("PFR carries team code %r, which does not map.", code)
        return code
