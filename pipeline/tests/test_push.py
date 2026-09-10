"""The transport, and specifically what it does when the network wobbles.

`--health` runs as a hard gate in CI: a build that cannot reach the site stops
before it spends Odds API credits. That makes a single dropped connection
expensive -- one cost the 02:45 UTC build on 2026-09-08, with green runs either
side of it. So the probe retries on the same terms a push does, and these tests
pin those terms down: transient failures are retried, and a rejected token is
not, because repeating a 403 only wastes the gate's time.
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest
import respx

from pipeline import config, push


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Retries back off exponentially; tests should not actually wait."""
    monkeypatch.setattr(push.time, "sleep", lambda _: None)


@pytest.fixture(autouse=True)
def _site(monkeypatch):
    monkeypatch.setattr(config, "WP_SITE_URL", "https://example.test")
    monkeypatch.setattr(config, "wp_token", lambda: "token")


HEALTH = "https://example.test/wp-json/trinity-rundown/v1/health"


@respx.mock
def test_health_returns_the_body_when_the_site_answers():
    respx.get(HEALTH).mock(return_value=httpx.Response(200, json={"ok": True}))

    assert push.health() == {"ok": True}


@respx.mock
def test_health_retries_a_dropped_connection_and_succeeds():
    route = respx.get(HEALTH).mock(
        side_effect=[
            httpx.ConnectError("connection reset"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    assert push.health() == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_health_retries_a_5xx_and_succeeds():
    route = respx.get(HEALTH).mock(
        side_effect=[
            httpx.Response(502, text="bad gateway"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    assert push.health() == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_health_gives_up_after_the_configured_attempts():
    route = respx.get(HEALTH).mock(return_value=httpx.Response(502, text="down"))

    with pytest.raises(push.PushError):
        push.health()

    assert route.call_count == config.HTTP_RETRIES


@respx.mock
def test_health_does_not_retry_a_rejected_token():
    """A 403 is the request being wrong, not the network. Repeating it only
    delays the failure the operator needs to see."""
    route = respx.get(HEALTH).mock(return_value=httpx.Response(403, text="nope"))

    with pytest.raises(push.PushError, match="403"):
        push.health()

    assert route.call_count == 1


# --------------------------------------------------------------------------
# The write path. Same retry discipline, and the one that matters most: a
# rejected token must not be repeated, because `push_week` is what runs
# hourly against staging.
# --------------------------------------------------------------------------

WEEK = "https://example.test/wp-json/trinity-rundown/v1/week"


@dataclasses.dataclass
class _Payload:
    """Enough of a WeekPayload for the transport.

    `push_week` reads `season` and `week` for its log line and calls `wire()`
    for the body; nothing here is testing the schema, which has its own tests.
    Standing in for it keeps these tests about HTTP behaviour and stops them
    breaking every time the payload grows a field.
    """

    season: int = 2026
    week: int = 1

    def wire(self) -> dict:
        return {"season": self.season, "week": self.week, "games": []}


def _payload():
    return _Payload()


@respx.mock
def test_push_retries_a_5xx_and_succeeds():
    route = respx.post(WEEK).mock(
        side_effect=[
            httpx.Response(503, text="unavailable"),
            httpx.Response(200, json={"inserted": 0, "updated": 16, "openers_set": 0}),
        ]
    )

    assert push.push_week(_payload())["updated"] == 16
    assert route.call_count == 2


@respx.mock
def test_push_does_not_retry_a_rejected_token():
    route = respx.post(WEEK).mock(return_value=httpx.Response(403, text="nope"))

    with pytest.raises(push.PushError, match="403"):
        push.push_week(_payload())

    assert route.call_count == 1


# ---------------------------------------------------------------------------
# Where the token is allowed to go
# ---------------------------------------------------------------------------
#
# The bearer token is sent with the request, so the destination has to be
# checked before the request is built rather than by whatever answers it. The
# endpoint's own is_ssl() guard rejects a cleartext call, but only after the
# credential has already crossed the wire -- and a WP_SITE_URL pointing
# somewhere unintended is exactly the mistake the cutover runbook is trying to
# catch. httpx does not follow redirects by default, so an http:// value never
# silently upgrades either.


@pytest.mark.parametrize(
    "site_url",
    [
        "http://example.test",
        "HTTP://example.test",
        "example.test",
        "//example.test",
        "ftp://example.test",
    ],
)
def test_endpoint_refuses_to_send_the_token_anywhere_but_https(monkeypatch, site_url):
    monkeypatch.setattr(config, "WP_SITE_URL", site_url)

    with pytest.raises(push.PushError, match="WP_SITE_URL"):
        push.endpoint("health")


def test_endpoint_names_the_variable_and_the_value_it_got(monkeypatch):
    """The operator reading this is mid-runbook, so the message has to be actionable."""
    monkeypatch.setattr(config, "WP_SITE_URL", "http://rundown.example")

    with pytest.raises(push.PushError) as caught:
        push.endpoint("week")

    message = str(caught.value)
    assert "WP_SITE_URL" in message
    assert "http://rundown.example" in message


def test_endpoint_still_builds_an_https_url(monkeypatch):
    monkeypatch.setattr(config, "WP_SITE_URL", "https://example.test")

    assert push.endpoint("week") == "https://example.test/wp-json/trinity-rundown/v1/week"


def test_endpoint_accepts_https_whatever_the_case(monkeypatch):
    monkeypatch.setattr(config, "WP_SITE_URL", "HTTPS://example.test")

    assert push.endpoint("health").endswith("/wp-json/trinity-rundown/v1/health")


def test_an_unset_site_url_still_says_so(monkeypatch):
    """The empty case predates the scheme guard and keeps its own message."""
    monkeypatch.setattr(config, "WP_SITE_URL", "")

    with pytest.raises(push.PushError, match="not set"):
        push.endpoint("health")
