"""The projections sheet, parsed into each team's top plays.

What has to hold:

1. Ranking is by percentage edge, not raw difference. Stats live on different
   scales, and a raw ranking would fill every table with passing yardage.
2. Only Standard lines count. Goblin and Demon lines are left out by the
   team's decision, so dropping them is not worth a warning.
3. A hand-kept sheet goes wrong a row at a time. A bad row costs that row and
   a warning; only a sheet that cannot be read at all raises, because "could
   not read it" must never be published as "no plays this week".
"""

from __future__ import annotations

import httpx
import pytest
import respx

from pipeline.sources import projections as projections_source
from pipeline.sources.projections import ProjectionsUnavailable, parse
from pipeline.sources.team_map import UnmappedTeamError

URL = "https://docs.google.com/spreadsheets/d/e/sheet/pub?gid=1&single=true&output=csv"

HEADER = "Site,Name,Position,Team,Statistic,Line,Proj,Type,Play The,Diff"

_TEAMS = {"SEA": "SEA", "NE": "NE", "LAR": "LA", "LA": "LA"}


@pytest.fixture(autouse=True)
def offline_team_map(monkeypatch):
    """The real map reads nflverse; these tests only need a handful of teams."""

    def to_abbr(name):
        try:
            return _TEAMS[name.upper()]
        except KeyError:
            raise UnmappedTeamError(name) from None

    monkeypatch.setattr(projections_source, "to_abbr", to_abbr)


def row(
    name="Jaxon Smith-Njigba",
    team="SEA",
    stat="Receiving Yards",
    line="70.5",
    proj="80.5",
    tier="Standard",
    play="Over",
    site="PrizePicks",
    position="WR",
):
    return f"{site},{name},{position},{team},{stat},{line},{proj},{tier},{play},0"


def sheet(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


# ---------------------------------------------------------------------------
# Parsing and ranking
# ---------------------------------------------------------------------------


def test_a_row_becomes_a_play():
    plays, warnings = parse(sheet(row()))

    [play] = plays["SEA"]
    assert play.player == "Jaxon Smith-Njigba"
    assert play.position == "WR"
    assert play.statistic == "Receiving Yards"
    assert play.play == "over"
    assert play.line == 70.5
    assert play.projection == 80.5
    assert play.site == "PrizePicks"
    assert warnings == []


def test_diff_and_edge_are_computed_not_read_from_the_sheet():
    """The sheet's Diff column is a formula someone can break; ours is not."""
    plays, _ = parse(sheet(row(line="50", proj="40", play="Under")))

    [play] = plays["SEA"]
    assert play.diff == -10
    assert play.edge == pytest.approx(0.2)


def test_headers_match_in_any_order_and_any_case():
    header = "play the,TEAM,proj,Line,statistic,Name,type"
    body = "Over,SEA,80.5,70.5,Receiving Yards,Jaxon Smith-Njigba,standard"

    plays, _ = parse(sheet(body, header=header))

    [play] = plays["SEA"]
    assert play.player == "Jaxon Smith-Njigba"
    assert play.site is None
    assert play.position is None


def test_a_receptions_edge_beats_a_bigger_yardage_difference():
    plays, _ = parse(
        sheet(
            row(name="Yards Guy", stat="Receiving Yards", line="80.5", proj="88.5"),
            row(name="Catches Guy", stat="Receptions", line="4.5", proj="6"),
        )
    )

    assert [p.player for p in plays["SEA"]] == ["Catches Guy", "Yards Guy"]


def test_each_team_is_cut_to_four():
    rows = [row(name=f"Player {n}", proj=str(71 + n)) for n in range(6)]

    plays, _ = parse(sheet(*rows))

    assert [p.player for p in plays["SEA"]] == [f"Player {n}" for n in (5, 4, 3, 2)]


def test_teams_are_kept_apart_and_filtered_to_the_week():
    plays, _ = parse(
        sheet(row(team="SEA"), row(name="Drake Maye", team="NE"), row(team="LAR")),
        teams={"SEA", "NE"},
    )

    assert set(plays) == {"SEA", "NE"}


def test_sheet_abbreviations_map_onto_nflverse():
    plays, _ = parse(sheet(row(team="LAR")))

    assert set(plays) == {"LA"}


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------


def test_goblin_and_demon_lines_are_left_out_quietly():
    plays, warnings = parse(
        sheet(
            row(name="Standard Guy"),
            row(name="Goblin Guy", tier="Goblin", proj="200"),
            row(name="Demon Guy", tier="DEMON", proj="200"),
        )
    )

    assert [p.player for p in plays["SEA"]] == ["Standard Guy"]
    assert warnings == []


def test_a_blank_type_counts_as_standard():
    """What a sportsbook Site with no pick'em tier leaves in the column."""
    plays, _ = parse(sheet(row(tier="", site="DraftKings")))

    assert len(plays["SEA"]) == 1


def test_an_unknown_type_is_skipped_with_a_warning():
    plays, warnings = parse(sheet(row(name="Kept"), row(name="Odd", tier="Flex")))

    assert [p.player for p in plays["SEA"]] == ["Kept"]
    assert len(warnings) == 1
    assert "'Flex'" in warnings[0]


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


def test_a_play_listed_on_several_sites_keeps_the_best_edge():
    plays, _ = parse(
        sheet(
            row(site="PrizePicks", line="74.5"),
            row(site="Underdog", line="69.5"),
            row(site="DraftKings", line="72.5", tier=""),
        )
    )

    [play] = plays["SEA"]
    assert play.site == "Underdog"
    assert play.line == 69.5


def test_dedupe_ignores_spacing_and_case_in_the_name_and_stat():
    plays, _ = parse(
        sheet(
            row(name="Jaxon Smith-Njigba", stat="Receiving Yards"),
            row(name="jaxon smith njigba", stat="receiving yards", line="65.5"),
        )
    )

    assert len(plays["SEA"]) == 1
    assert plays["SEA"][0].line == 65.5


# ---------------------------------------------------------------------------
# Bad rows
# ---------------------------------------------------------------------------


def test_an_unmapped_team_is_skipped_with_a_warning():
    plays, warnings = parse(sheet(row(), row(team="Gotham"), row(team="Gotham")))

    assert set(plays) == {"SEA"}
    assert len(warnings) == 1
    assert "'Gotham' (2)" in warnings[0]


@pytest.mark.parametrize(
    "bad",
    [
        {"line": "n/a"},
        {"proj": ""},
        {"line": "0"},
        {"play": "Push"},
        {"stat": ""},
    ],
)
def test_a_row_that_cannot_be_ranked_is_skipped_and_counted(bad):
    plays, warnings = parse(sheet(row(name="Kept"), row(name="Bad", **bad)))

    assert [p.player for p in plays["SEA"]] == ["Kept"]
    assert len(warnings) == 1
    assert "1 row(s) skipped" in warnings[0]


def test_blank_spacer_rows_are_ignored_without_a_warning():
    plays, warnings = parse(sheet(row(), ",,,,,,,,,", ""))

    assert len(plays["SEA"]) == 1
    assert warnings == []


def test_a_play_against_its_own_projection_is_skipped_and_named():
    """Edge is a distance, so this row would otherwise rank as a strong Over
    on a projection that says Under."""
    plays, warnings = parse(
        sheet(row(name="Kept"), row(line="70.5", proj="50.5", play="Over"))
    )

    assert [p.player for p in plays["SEA"]] == ["Kept"]
    assert len(warnings) == 1
    assert "Jaxon Smith-Njigba" in warnings[0]


def test_a_contradicted_site_does_not_hide_a_good_one_for_the_same_prop():
    plays, _ = parse(
        sheet(
            row(site="PrizePicks", line="70.5", proj="80.5", play="Over"),
            row(site="Underdog", line="90.5", proj="80.5", play="Over"),
        )
    )

    [play] = plays["SEA"]
    assert play.site == "PrizePicks"


def test_numbers_with_thousands_separators_parse():
    plays, _ = parse(sheet(row(line='"1,000.5"', proj='"1,100"')))

    assert plays["SEA"][0].line == 1000.5


# ---------------------------------------------------------------------------
# An unreadable sheet raises
# ---------------------------------------------------------------------------


def test_a_missing_column_raises():
    with pytest.raises(ProjectionsUnavailable, match="type"):
        parse(sheet("SEA", header="Name,Team,Statistic,Line,Proj,Play The"))


def test_an_empty_body_raises():
    with pytest.raises(ProjectionsUnavailable):
        parse("")


def test_an_html_body_raises():
    """What Google serves once a sheet is unpublished."""
    with pytest.raises(ProjectionsUnavailable, match="HTML"):
        parse("<!DOCTYPE html><html><body>Sign in</body></html>")


@respx.mock
def test_fetch_follows_googles_redirect():
    respx.get(URL).mock(
        return_value=httpx.Response(307, headers={"Location": "https://sheets.example/csv"})
    )
    respx.get("https://sheets.example/csv").mock(
        return_value=httpx.Response(200, content=("﻿" + sheet(row())).encode())
    )

    plays, _ = projections_source.fetch(URL)

    # The byte-order mark would otherwise glue itself to "Site".
    assert plays["SEA"][0].site == "PrizePicks"


@respx.mock
def test_a_server_error_raises():
    respx.get(URL).mock(return_value=httpx.Response(503))

    with pytest.raises(ProjectionsUnavailable):
        projections_source.fetch(URL)


@respx.mock
def test_a_connection_error_raises():
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(ProjectionsUnavailable):
        projections_source.fetch(URL)
