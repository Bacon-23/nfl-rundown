"""Trinity's own prop projections, from the Google Sheet the team maintains.

The sheet's projections tab is published to the web as CSV -- it already is,
because the site's existing projections table reads it -- and the URL lives in
the `PROJECTIONS_CSV_URL` secret. The team updates it a few times a week with
no set timing, so the hourly build simply rereads it every run.

Each row is one player's line on one Site, against Trinity's projection. This
module keeps the best few per team, ranked by percentage edge:

    edge = |Proj - Line| / Line

Percentage rather than raw difference because the stats live on different
scales. Two yards over a 4.5-reception line is nothing like two yards over an
82.5-yard one, and ranking by raw difference would fill every table with
passing yardage.

Only Standard lines are used. Goblin and Demon lines are discounted or boosted
against Standard on the pick'em sites, so their edges are not comparable, and
the team chose to leave them out rather than rank them alongside.

The sheet is maintained by hand, so everything here is defensive: headers are
matched by name, a bad row costs that row and a warning, and only a feed that
cannot be read at all raises.
"""

from __future__ import annotations

import csv
import io
import logging
import unicodedata
from collections import Counter

import httpx

from pipeline import config
from pipeline.schema import PlayRow
from pipeline.sources.team_map import UnmappedTeamError, to_abbr

log = logging.getLogger(__name__)

#: Sheet header -> field, keyed by the header with case, spacing and
#: punctuation stripped, so "Play The", "play_the" and "PLAY THE" all match.
_HEADERS: dict[str, str] = {
    "site": "site",
    "name": "name",
    "position": "position",
    "team": "team",
    "statistic": "statistic",
    "line": "line",
    "proj": "proj",
    "type": "type",
    "playthe": "play",
}

#: Without these a row cannot be placed, ranked, or filtered. Type is required
#: even though most rows say Standard: with the column gone, every Goblin and
#: Demon line would pass as Standard and nobody would notice.
_REQUIRED = ("name", "team", "statistic", "line", "proj", "type", "play")

#: Tiers that are kept. Blank counts as Standard, which is what a sportsbook
#: Site with no pick'em tier leaves in the column.
_STANDARD = {"", "standard"}

#: Tiers that are dropped without comment -- the team's decision, not a fault.
_EXCLUDED = {"goblin", "demon"}

_PLAYS = {"over": "over", "o": "over", "under": "under", "u": "under"}


class ProjectionsUnavailable(RuntimeError):
    """The sheet could not be read. Distinct from 'no plays this week'."""


def fetch(
    url: str, teams: set[str] | None = None
) -> tuple[dict[str, list[PlayRow]], list[str]]:
    """The top plays per team abbreviation, plus warnings about the sheet.

    A team in `teams` with no usable rows is simply absent from the result.

    Raises:
        ProjectionsUnavailable: if the sheet cannot be fetched or parsed as the
            projections table. Callers must not turn this into empty tables --
            a transient Google error would otherwise wipe every game's plays.
    """
    return parse(_get(url), teams)


def parse(
    text: str, teams: set[str] | None = None
) -> tuple[dict[str, list[PlayRow]], list[str]]:
    """Parse the CSV body. Split from `fetch` so tests need no network."""
    if text.lstrip().startswith("<"):
        # What Google serves for a sheet that is not (or no longer) published.
        raise ProjectionsUnavailable("The projections URL returned HTML, not CSV.")

    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        raise ProjectionsUnavailable("The projections sheet is empty.")

    index: dict[str, int] = {}
    for position, cell in enumerate(header):
        field = _HEADERS.get(_key(cell))
        if field and field not in index:
            index[field] = position

    missing = [field for field in _REQUIRED if field not in index]
    if missing:
        raise ProjectionsUnavailable(
            f"The projections sheet is missing column(s): {', '.join(missing)}."
        )

    warnings: list[str] = []
    unmapped: Counter[str] = Counter()
    unknown_tiers: Counter[str] = Counter()
    bad_rows = 0
    mismatches: list[str] = []

    #: (team, player, statistic) -> the best Site's row for it.
    best: dict[tuple[str, str, str], PlayRow] = {}

    for cells in reader:
        row = {field: _cell(cells, at) for field, at in index.items()}

        if not row["name"] and not row["team"]:
            continue  # a blank spacer row

        tier = row["type"].casefold()
        if tier in _EXCLUDED:
            continue
        if tier not in _STANDARD:
            unknown_tiers[row["type"]] += 1
            continue

        try:
            abbr = to_abbr(row["team"])
        except UnmappedTeamError:
            unmapped[row["team"]] += 1
            continue

        if teams is not None and abbr not in teams:
            continue

        line = _number(row["line"])
        projection = _number(row["proj"])
        play = _PLAYS.get(row["play"].casefold())
        if line is None or projection is None or line <= 0 or play is None or not row["statistic"]:
            bad_rows += 1
            continue

        diff = projection - line
        if (play == "over" and diff < 0) or (play == "under" and diff > 0):
            # Almost certainly a sheet error. The edge is a distance, so left
            # in, this row would rank as a strong play in the direction its own
            # projection argues against -- skip it and name it instead.
            mismatches.append(f"{row['name']} {row['statistic']} ({play} {line:g})")
            continue

        candidate = PlayRow(
            player=row["name"],
            position=row.get("position") or None,
            statistic=row["statistic"],
            play=play,
            line=line,
            projection=projection,
            diff=round(diff, 2),
            edge=round(abs(diff) / line, 4),
            site=row.get("site") or None,
        )

        key = (abbr, _key(row["name"]), _key(row["statistic"]))
        current = best.get(key)
        if current is None or candidate.edge > current.edge:
            best[key] = candidate

    by_team: dict[str, list[PlayRow]] = {}
    for (abbr, _, _), play_row in best.items():
        by_team.setdefault(abbr, []).append(play_row)

    for abbr, rows in by_team.items():
        rows.sort(key=lambda r: (-r.edge, r.player))
        by_team[abbr] = rows[: config.TOP_PLAYS_PER_TEAM]

    if unmapped:
        names = ", ".join(f"{name!r} ({count})" for name, count in unmapped.most_common())
        warnings.append(f"Projections: unmapped team name(s) skipped: {names}.")
    if unknown_tiers:
        tiers = ", ".join(f"{tier!r} ({count})" for tier, count in unknown_tiers.most_common())
        warnings.append(f"Projections: rows with an unrecognised Type skipped: {tiers}.")
    if bad_rows:
        warnings.append(
            f"Projections: {bad_rows} row(s) skipped for a missing or non-numeric Line or "
            "Proj, a Line of zero, or a Play The that is not Over or Under."
        )
    if mismatches:
        shown = "; ".join(mismatches[:5])
        more = f" and {len(mismatches) - 5} more" if len(mismatches) > 5 else ""
        warnings.append(
            f"Projections: Play The disagrees with Proj vs. Line for {shown}{more}. "
            "Skipped."
        )

    return by_team, warnings


def _get(url: str) -> str:
    try:
        with httpx.Client(
            timeout=config.HTTP_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
            # Google answers a published-CSV link with a redirect to
            # googleusercontent.com, so not following it reads nothing.
            follow_redirects=True,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ProjectionsUnavailable(f"Projections request failed: {exc}") from exc

    # utf-8-sig drops the byte-order mark Sheets sometimes leads with, which
    # would otherwise glue itself to the first header and hide the Site column.
    return response.content.decode("utf-8-sig", errors="replace")


def _cell(cells: list[str], at: int) -> str:
    return cells[at].strip() if at < len(cells) else ""


def _key(value: str) -> str:
    """Casefold and strip everything but letters and digits."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if ch.isalnum()).casefold()


def _number(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None
