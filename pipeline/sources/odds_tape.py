"""Record and replay Odds API responses.

Staging needs to exercise the real parsing path without spending credits on
every run, and without test results shifting under you between runs. So one
live capture is committed as a fixture and replayed thereafter.

A tape sits between `odds.py` and httpx. It is the only thing that knows
whether a response came off the wire or off disk; everything downstream parses
identical `httpx.Response` objects either way.

The API key is never part of a tape key and never written to a fixture --
fixtures are committed to the repo.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

#: Response headers worth keeping. Everything else is noise, and an allowlist
#: means a future API change cannot leak something sensitive into a committed
#: fixture.
_KEEP_HEADERS = ("x-requests-remaining", "x-requests-used")


class StaleFixtureError(RuntimeError):
    """A replay fixture no longer matches the games being built.

    Deliberately not an `OddsUnavailable`: that one is caught and downgraded to
    the nflverse fallback, which is exactly the silent pass this error exists
    to prevent.
    """


def tape_key(url: str, params: dict) -> str:
    """Identify a call by path and markets, never by credential.

    The API key lives in the query string, so keying on the full URL would
    write it into a committed file and invalidate every fixture on key
    rotation.
    """
    path = urlparse(url).path
    markets = params.get("markets", "")
    book = params.get("bookmakers", params.get("regions", ""))
    return f"GET {path}?markets={markets}&book={book}"


class LiveTape:
    """Straight passthrough to the network."""

    replaying = False

    def get(self, client: httpx.Client, url: str, params: dict) -> httpx.Response:
        return client.get(url, params=params)

    def save(self) -> None:
        return None


class RecordingTape(LiveTape):
    """Fetch live, and keep a copy of everything for later replay."""

    def __init__(self, path: Path, meta: dict | None = None):
        self.path = Path(path)
        self.meta = meta or {}
        self.entries: dict[str, dict] = {}

    def get(self, client: httpx.Client, url: str, params: dict) -> httpx.Response:
        response = super().get(client, url, params)

        # A non-JSON error page is still worth recording as a bare status, so
        # the replayed run sees the same failure the live one did.
        body = None
        with contextlib.suppress(ValueError):
            body = response.json()

        self.entries[tape_key(url, params)] = {
            "status": response.status_code,
            "headers": {
                k: v for k, v in response.headers.items() if k.lower() in _KEEP_HEADERS
            },
            "json": body,
        }

        return response

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "recorded_at": datetime.now(UTC).isoformat(),
            **self.meta,
            "entries": self.entries,
        }
        self.path.write_text(json.dumps(document, indent=2), encoding="utf-8")


class ReplayTape:
    """Serve recorded responses. Never touches the network."""

    replaying = True

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise StaleFixtureError(
                f"No odds fixture at {self.path}. Create one with "
                f"--record-odds {self.path}."
            )

        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.recorded_at = document.get("recorded_at", "unknown")
        self.entries: dict[str, dict] = document.get("entries", {})

        if not self.entries:
            raise StaleFixtureError(f"Odds fixture {self.path} contains no responses.")

    def get(self, client: httpx.Client, url: str, params: dict) -> httpx.Response:
        entry = self.entries.get(tape_key(url, params))
        request = httpx.Request("GET", url)

        if entry is None:
            # A per-event market the capture did not include. odds.py already
            # treats a 4xx here as "no posted market" and derives instead.
            return httpx.Response(404, json={"message": "not in fixture"}, request=request)

        return httpx.Response(
            entry.get("status", 200),
            headers=entry.get("headers", {}),
            json=entry.get("json"),
            request=request,
        )

    def save(self) -> None:
        return None


def check_match_rate(tape, matched: int, total: int, floor: float) -> None:
    """Fail a replayed run whose fixture has gone stale.

    Replay matches events to games by team pair, exactly as the live path does.
    So a Week 1 fixture replayed in Week 5 matches nothing, every game quietly
    falls back to nflverse lines, and a broken parser still looks green. A test
    that cannot fail is not a test.
    """
    if not getattr(tape, "replaying", False) or total == 0:
        return

    rate = matched / total
    if rate >= floor:
        return

    raise StaleFixtureError(
        f"Odds fixture matched only {matched}/{total} games "
        f"(recorded {getattr(tape, 'recorded_at', 'unknown')}). "
        f"It is stale -- re-record with --record-odds."
    )
