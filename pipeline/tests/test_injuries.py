"""Injury parsing from ESPN's public feed.

Two behaviours here are load-bearing:

1. The feed returns recent *news items*, not an injury report. Most carry
   status "Active" and describe a signing or a roster move. Publishing those
   as injuries would be wrong on the face of it.
2. A fetch failure must raise rather than return an empty list. "I could not
   reach ESPN" and "nobody is hurt" are different claims, and conflating them
   would wipe a good injury table off a published page.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from pipeline import config
from pipeline.sources import injuries as injuries_source
from pipeline.sources.injuries import InjuriesUnavailable

ESPN = config.ESPN_INJURIES_URL


def item(
    player="Zach Charbonnet",
    position="RB",
    status="Out",
    kind="Knee - ACL",
    comment="Charbonnet remains on the active/PUP list ahead of Sunday.",
):
    return {
        "status": status,
        "shortComment": comment,
        "athlete": {
            "displayName": player,
            "position": {"abbreviation": position} if position else None,
        },
        "details": {"type": kind} if kind else {},
    }


def feed(*teams):
    """teams: (display name, [items]) pairs."""
    return {"injuries": [{"displayName": name, "injuries": items} for name, items in teams]}


def mock_feed(payload):
    return respx.get(ESPN).mock(return_value=httpx.Response(200, json=payload))


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


@respx.mock
def test_active_players_are_not_injuries():
    """The feed's default status. Most of what it tags is roster news."""
    mock_feed(
        feed(
            (
                "Seattle Seahawks",
                [
                    item(player="Zach Charbonnet", status="Out"),
                    item(
                        player="Trevon Diggs",
                        status="Active",
                        kind=None,
                        comment="The Seahawks are slated to sign Diggs to a one-year deal.",
                    ),
                ],
            )
        )
    )

    rows = injuries_source.fetch({"SEA"})["SEA"]

    assert [r.player for r in rows] == ["Zach Charbonnet"]


@respx.mock
def test_only_the_requested_teams_come_back():
    mock_feed(
        feed(
            ("Seattle Seahawks", [item()]),
            ("Green Bay Packers", [item(player="Jordan Love", position="QB")]),
        )
    )

    result = injuries_source.fetch({"SEA"})

    assert set(result) == {"SEA"}


@respx.mock
def test_an_unmappable_team_is_skipped_not_fatal():
    """Unlike odds, a missing injury team costs one table, not a blank line."""
    mock_feed(feed(("Seattle Seahawks", [item()]), ("Toronto Argonauts", [item()])))

    result = injuries_source.fetch()

    assert "SEA" in result
    assert len(result) == 1


@respx.mock
def test_the_per_team_cap_keeps_the_most_serious():
    many = [
        item(player=f"Questionable {i}", status="Questionable")
        for i in range(injuries_source.MAX_PER_TEAM + 4)
    ]
    many.append(item(player="Ruled Out", status="Out"))

    mock_feed(feed(("Seattle Seahawks", many)))

    rows = injuries_source.fetch({"SEA"})["SEA"]

    assert len(rows) == injuries_source.MAX_PER_TEAM
    assert rows[0].player == "Ruled Out"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


@respx.mock
def test_rows_are_ordered_by_severity():
    mock_feed(
        feed(
            (
                "Seattle Seahawks",
                [
                    item(player="Q Player", status="Questionable"),
                    item(player="IR Player", status="Injured Reserve"),
                    item(player="Out Player", status="Out"),
                    item(player="D Player", status="Doubtful"),
                ],
            )
        )
    )

    rows = injuries_source.fetch({"SEA"})["SEA"]

    # Out before IR: ruled out this week is news, IR is weeks old.
    assert [r.player for r in rows] == ["Out Player", "IR Player", "D Player", "Q Player"]


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


@respx.mock
def test_the_mockups_example_row_round_trips():
    mock_feed(feed(("Seattle Seahawks", [item()])))

    row = injuries_source.fetch({"SEA"})["SEA"][0]

    assert row.team == "SEA"
    assert row.player == "Zach Charbonnet"
    assert row.position == "RB"
    assert row.status == "Out"
    assert row.note == "Knee - ACL"
    assert row.comment.startswith("Charbonnet")


@respx.mock
def test_undisclosed_injuries_produce_no_note():
    """Better an empty cell than the word "Undisclosed" in every row."""
    mock_feed(feed(("Seattle Seahawks", [item(kind="Undisclosed")])))

    assert injuries_source.fetch({"SEA"})["SEA"][0].note is None


@respx.mock
def test_a_comment_that_just_repeats_the_status_is_dropped():
    """The feed often sends "ir" or "questionable" as the whole comment."""
    mock_feed(
        feed(
            (
                "Seattle Seahawks",
                [
                    item(player="A", status="Injured Reserve", comment="ir"),
                    item(player="B", status="Questionable", comment="questionable"),
                ],
            )
        )
    )

    rows = injuries_source.fetch({"SEA"})["SEA"]

    assert all(r.comment is None for r in rows)


@respx.mock
def test_a_player_without_a_name_is_skipped():
    payload = feed(("Seattle Seahawks", [item(), item()]))
    payload["injuries"][0]["injuries"][1]["athlete"] = {}

    mock_feed(payload)

    assert len(injuries_source.fetch({"SEA"})["SEA"]) == 1


@respx.mock
def test_a_missing_position_is_tolerated():
    mock_feed(feed(("Seattle Seahawks", [item(position=None)])))

    assert injuries_source.fetch({"SEA"})["SEA"][0].position is None


# ---------------------------------------------------------------------------
# Failure must be distinguishable from "nobody is hurt"
# ---------------------------------------------------------------------------


@respx.mock
def test_a_server_error_raises_rather_than_returning_empty():
    respx.get(ESPN).mock(return_value=httpx.Response(503))

    with pytest.raises(InjuriesUnavailable):
        injuries_source.fetch({"SEA"})


@respx.mock
def test_a_network_error_raises():
    respx.get(ESPN).mock(side_effect=httpx.ConnectError("dns"))

    with pytest.raises(InjuriesUnavailable):
        injuries_source.fetch({"SEA"})


@respx.mock
def test_malformed_json_raises():
    respx.get(ESPN).mock(return_value=httpx.Response(200, text="<html>nope</html>"))

    with pytest.raises(InjuriesUnavailable):
        injuries_source.fetch({"SEA"})


@respx.mock
def test_an_empty_feed_raises_rather_than_reporting_a_healthy_league():
    mock_feed({"injuries": []})

    with pytest.raises(InjuriesUnavailable):
        injuries_source.fetch({"SEA"})


@respx.mock
def test_a_team_with_genuinely_no_injuries_returns_an_empty_list():
    """This is the case that must NOT raise -- it is real information."""
    mock_feed(
        feed(
            ("Seattle Seahawks", [item(status="Active", kind=None)]),
            ("New England Patriots", [item(player="Hurt Guy")]),
        )
    )

    result = injuries_source.fetch({"SEA", "NE"})

    assert result["SEA"] == []
    assert len(result["NE"]) == 1


# ---------------------------------------------------------------------------
# Practice enrichment
# ---------------------------------------------------------------------------


def test_practice_enrichment_never_raises(monkeypatch):
    """nflverse's injury feed is dead weight most weeks; it must stay optional."""

    def boom(*args, **kwargs):
        raise RuntimeError("nflverse exploded")

    monkeypatch.setattr(injuries_source, "_practice_lookup", boom)

    rows = {"SEA": []}
    assert injuries_source.enrich_with_practice(rows, 2026, 1) == 0


def test_practice_status_is_abbreviated_for_a_narrow_column():
    shorten = injuries_source._shorten

    assert shorten("Did Not Participate In Practice") == "DNP"
    assert shorten("Limited Participation in Practice") == "LP"
    assert shorten("Full Participation in Practice") == "FP"
    # Anything unrecognised passes through rather than being silently dropped.
    assert shorten("Rest") == "Rest"
