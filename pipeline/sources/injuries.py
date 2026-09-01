"""Injury reports.

Primary source is ESPN's public injuries endpoint, which returns all 32 teams
in one request with a status and a beat-writer note. It is undocumented and
unsupported, so everything here is defensive.

nflverse's `load_injuries()` carries practice participation (DNP / LP / FP),
which ESPN does not, and which is what makes a note like "Groin -- DNP Wed."
possible. It is enrichment only: that feed's 2025 file last updated in March
2026, so it can never be a hard dependency.

Two properties of the ESPN feed drive the design:

1. It returns the 25 most recent *news items* per team, not an injury report.
   Most carry status "Active" -- a signing, a return, a roster note -- and have
   nothing to do with availability. Those are dropped.
2. `shortComment` is inconsistent. Sometimes a full sentence, sometimes the
   literal string "ir" or "questionable". So the note is built from the
   structured injury type, and the comment is kept separately for the editor.
"""

from __future__ import annotations

import logging

import httpx
import polars as pl

from pipeline import config
from pipeline.schema import InjuryRow
from pipeline.sources.team_map import UnmappedTeamError, to_abbr

log = logging.getLogger(__name__)

#: Statuses that affect whether a player takes the field, in display order.
#:
#: "Active" is excluded on purpose -- it is the feed's default for any news
#: item, so most of what it tags is a signing or a roster note, not an injury.
#:
#: "Out" outranks "Injured Reserve" deliberately: a player ruled out this week
#: is news for this matchup, while someone on IR left the picture weeks ago.
_REPORTABLE = {
    "out": 0,
    "injured reserve": 1,
    "doubtful": 2,
    "suspension": 3,
    "questionable": 4,
    "probable": 5,
}

#: Injury types that carry no information, so they are left out of the note
#: rather than printed as "Undisclosed".
_EMPTY_TYPES = {"", "undisclosed", "not specified", "none"}

#: Column names nflverse has used for practice participation across versions.
_PRACTICE_COLUMNS = ("practice_status", "practice_primary_injury", "report_status")

#: Cap per team, so one club's long report cannot swamp the table. Ordering is
#: by severity, so the cap only ever drops the least consequential rows.
MAX_PER_TEAM = 6


class InjuriesUnavailable(RuntimeError):
    """ESPN could not be reached. Distinct from 'no injuries reported'."""


def fetch(teams: set[str] | None = None) -> dict[str, list[InjuryRow]]:
    """Injury rows per team abbreviation.

    Raises:
        InjuriesUnavailable: if the feed cannot be read. Callers must not turn
            this into an empty list -- "I don't know" and "nobody is hurt" are
            different claims, and conflating them would wipe a good table off
            the page on a transient network error.
    """
    payload = _get()

    by_team: dict[str, list[InjuryRow]] = {}

    for entry in payload.get("injuries", []):
        name = entry.get("displayName")
        if not name:
            continue

        try:
            abbr = to_abbr(name)
        except UnmappedTeamError:
            log.warning("ESPN injuries: unmapped team %r, skipped.", name)
            continue

        if teams is not None and abbr not in teams:
            continue

        rows = [
            row
            for row in (_to_row(abbr, item) for item in entry.get("injuries", []))
            if row is not None
        ]

        rows.sort(key=lambda r: (_severity(r.status), r.player))
        by_team[abbr] = rows[:MAX_PER_TEAM]

    if not by_team:
        raise InjuriesUnavailable("ESPN returned no usable teams.")

    return by_team


def _get() -> dict:
    try:
        with httpx.Client(
            timeout=config.HTTP_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
        ) as client:
            response = client.get(config.ESPN_INJURIES_URL)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise InjuriesUnavailable(f"ESPN injuries request failed: {exc}") from exc


def _to_row(team: str, item: dict) -> InjuryRow | None:
    status = str(item.get("status") or "").strip()
    if status.casefold() not in _REPORTABLE:
        return None

    athlete = item.get("athlete") or {}
    player = athlete.get("displayName")
    if not player:
        return None

    position = (athlete.get("position") or {}).get("abbreviation")
    details = item.get("details") or {}

    return InjuryRow(
        team=team,
        player=player,
        position=position,
        status=status,
        note=_note(details),
        comment=_comment(item, status),
    )


def _note(details: dict) -> str | None:
    """The short injury descriptor, e.g. "Knee" or "Knee - ACL"."""
    kind = str(details.get("type") or "").strip()
    if kind.casefold() in _EMPTY_TYPES:
        return None
    return kind


def _comment(item: dict, status: str) -> str | None:
    """The beat-writer note, when there actually is one.

    The feed often repeats the status as the comment ("ir", "questionable"),
    which tells a reader nothing they cannot see in the status column.
    """
    comment = str(item.get("shortComment") or "").strip()
    if not comment:
        return None

    stripped = comment.rstrip(".").casefold()
    if stripped == status.casefold() or len(stripped) < 12:
        return None

    return comment


def _severity(status: str) -> int:
    return _REPORTABLE.get(status.casefold(), 99)


# ---------------------------------------------------------------------------
# Practice participation, from nflverse
# ---------------------------------------------------------------------------


def enrich_with_practice(
    rows_by_team: dict[str, list[InjuryRow]],
    season: int,
    week: int,
) -> int:
    """Add DNP/LP/FP to rows where nflverse has it. Returns how many matched.

    Best-effort by design. The feed stopped updating live after the 2024
    season, so this usually adds nothing and must never break a build.
    """
    try:
        practice = _practice_lookup(season, week)
    except Exception as exc:  # noqa: BLE001 - enrichment must never propagate
        log.debug("Practice participation unavailable: %s", exc)
        return 0

    if not practice:
        return 0

    matched = 0
    for team, rows in rows_by_team.items():
        for index, row in enumerate(rows):
            value = practice.get((team, row.player.casefold()))
            if not value:
                continue
            rows[index] = row.model_copy(update={"practice": value})
            matched += 1

    return matched


def _practice_lookup(season: int, week: int) -> dict[tuple[str, str], str]:
    import nflreadpy as nfl

    frame = nfl.load_injuries([season])
    if isinstance(frame, pl.LazyFrame):
        frame = frame.collect()

    columns = set(frame.columns)
    needed = {"team", "full_name", "week"}
    if not needed.issubset(columns):
        return {}

    status_col = next(
        (c for c in _PRACTICE_COLUMNS if c in columns),
        None,
    )
    if status_col is None:
        return {}

    frame = frame.filter(pl.col("week") == week)

    lookup: dict[tuple[str, str], str] = {}
    for row in frame.select(["team", "full_name", status_col]).iter_rows(named=True):
        value = row.get(status_col)
        if not value:
            continue
        lookup[(row["team"], str(row["full_name"]).casefold())] = _shorten(str(value))

    return lookup


def _shorten(practice_status: str) -> str:
    """nflverse spells practice participation out; the table has a narrow column."""
    text = practice_status.casefold()
    if "did not" in text or text == "dnp":
        return "DNP"
    if "limited" in text:
        return "LP"
    if "full" in text:
        return "FP"
    return practice_status
