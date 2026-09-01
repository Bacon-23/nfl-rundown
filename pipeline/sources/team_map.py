"""Mapping between The Odds API team names and nflverse abbreviations.

The Odds API returns full names ("Seattle Seahawks"); nflverse keys everything
on abbreviations ("SEA"). Every odds event has to land on exactly one nflverse
game, because an event that fails to join would publish a matchup with a blank
line -- a bug readers notice before we do.

So there is no permissive fallback here. An unmapped name raises.
"""

from __future__ import annotations

import functools
import unicodedata

import nflreadpy as nfl


class UnmappedTeamError(LookupError):
    """Raised when a team name cannot be resolved to an nflverse abbreviation."""


#: Franchises that moved or rebranded, plus the handful of names where a
#: sportsbook's spelling differs from nflverse's. nflverse keeps historical
#: abbreviations alive, so these collapse onto the current team.
_ALIASES: dict[str, str] = {
    "oakland raiders": "LV",
    "las vegas raiders": "LV",
    "san diego chargers": "LAC",
    "los angeles chargers": "LAC",
    "st louis rams": "LA",
    "st. louis rams": "LA",
    "los angeles rams": "LA",
    "washington redskins": "WAS",
    "washington football team": "WAS",
    "washington commanders": "WAS",
}


@functools.lru_cache(maxsize=1)
def _lookup() -> dict[str, str]:
    """Build name -> abbreviation from nflverse, then layer the aliases on top."""
    teams = nfl.load_teams()

    table: dict[str, str] = {}
    columns = set(teams.columns)

    # nflreadpy has changed these column names across versions, so take
    # whichever pair is present rather than assuming one layout.
    abbr_col = next((c for c in ("team_abbr", "team", "abbr") if c in columns), None)
    if abbr_col is None:
        raise RuntimeError(
            f"load_teams() has no abbreviation column: {sorted(columns)}"
        )

    candidates = ("team_name", "full_name", "team_nick", "team_nickname")
    name_cols = [c for c in candidates if c in columns]

    for row in teams.select([abbr_col, *name_cols]).iter_rows(named=True):
        abbr = row[abbr_col]
        if not abbr:
            continue
        table[_normalise(abbr)] = abbr
        for col in name_cols:
            if row.get(col):
                table[_normalise(row[col])] = abbr

    table.update(_ALIASES)
    return table


def _normalise(value: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", str(value))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in stripped)
    return " ".join(cleaned.split()).casefold()


def to_abbr(name: str) -> str:
    """Resolve a team name or abbreviation to its nflverse abbreviation.

    Raises:
        UnmappedTeamError: if the name is not recognised. Callers must not
            swallow this -- a dropped game is worse than a failed run.
    """
    key = _normalise(name)
    table = _lookup()

    if key in table:
        return table[key]

    # Books occasionally send just the nickname ("Seahawks").
    suffix_matches = {abbr for mapped, abbr in table.items() if mapped.endswith(f" {key}")}
    if len(suffix_matches) == 1:
        return suffix_matches.pop()

    raise UnmappedTeamError(
        f"Cannot map team name {name!r} to an nflverse abbreviation. "
        f"Add it to _ALIASES in pipeline/sources/team_map.py."
    )


@functools.lru_cache(maxsize=1)
def team_meta() -> dict[str, dict[str, str | None]]:
    """Display name, primary color, and logo per abbreviation, for the UI."""
    teams = nfl.load_teams()
    columns = set(teams.columns)

    abbr_col = next((c for c in ("team_abbr", "team", "abbr") if c in columns), None)
    name_col = next((c for c in ("team_name", "full_name") if c in columns), None)
    color_col = next((c for c in ("team_color", "primary_color") if c in columns), None)
    logo_cols = ("team_logo_espn", "team_logo_wikipedia", "logo")
    logo_col = next((c for c in logo_cols if c in columns), None)

    wanted = [c for c in (abbr_col, name_col, color_col, logo_col) if c]

    meta: dict[str, dict[str, str | None]] = {}
    for row in teams.select(wanted).iter_rows(named=True):
        abbr = row.get(abbr_col)
        if not abbr:
            continue
        meta[abbr] = {
            "name": row.get(name_col) if name_col else abbr,
            "color": row.get(color_col) if color_col else None,
            "logo": row.get(logo_col) if logo_col else None,
        }
    return meta


def clear_caches() -> None:
    """Drop memoised nflverse lookups. Used by tests."""
    _lookup.cache_clear()
    team_meta.cache_clear()
