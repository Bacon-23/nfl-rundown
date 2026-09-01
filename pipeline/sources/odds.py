"""Odds from The Odds API, with an nflverse fallback.

Credit arithmetic, from the v4 docs:

  bulk /odds          cost = markets x regions, returns every game in one call
  per-event /odds     cost = unique markets returned x regions, per event
  historical          cost = 10 x markets x regions

Passing `bookmakers=` instead of `regions=` keeps the multiplier at one. A
weekly build therefore costs 3 credits for the featured markets plus one per
game for team totals.

When the API is unreachable, over quota, or unconfigured, this module returns
lines derived from nflverse and marks the payload so the admin screen can warn
the writer before they publish.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import httpx

from pipeline import config
from pipeline.schema import Odds
from pipeline.sources.odds_tape import (
    LiveTape,
    RecordingTape,
    ReplayTape,
    check_match_rate,
)
from pipeline.sources.schedule import ScheduledGame
from pipeline.sources.team_map import UnmappedTeamError, to_abbr

log = logging.getLogger(__name__)

#: How far an odds event's commence time may sit from the scheduled kickoff and
#: still be considered the same game. Books post approximate times and
#: occasionally lag a reschedule, so this is deliberately loose; the team pair
#: is what actually identifies the game.
_KICKOFF_TOLERANCE = timedelta(hours=30)


class OddsUnavailable(RuntimeError):
    """The Odds API could not be used for this run."""


@dataclass
class OddsFetchResult:
    odds: dict[str, Odds]
    source: str
    quota_remaining: int | None = None
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []


def fetch(
    games: list[ScheduledGame],
    *,
    use_api: bool = True,
    record: Path | None = None,
    replay: Path | None = None,
) -> OddsFetchResult:
    """Odds for every game in `games`, keyed by nflverse game_id.

    Never raises for an API problem: falls back to nflverse lines and records
    a warning instead, because a page with slightly stale lines beats no page.

    A stale replay fixture is the exception. That one raises, because falling
    back silently is precisely the failure the fixture check exists to catch.

    Args:
        record: capture live responses to this path for later replay.
        replay: serve responses from this fixture instead of calling the API.
    """
    if not use_api:
        return _fallback(games, "Odds API disabled for this run (--no-odds-api).")

    tape = _make_tape(games, record=record, replay=replay)

    try:
        result = _from_api(games, tape)
    except OddsUnavailable as exc:
        log.warning("Odds API unavailable: %s", exc)
        return _fallback(games, str(exc))
    except httpx.HTTPError as exc:
        log.warning("Odds API transport error: %s", exc)
        return _fallback(games, f"Odds API transport error: {exc}")

    # Only persist a recording once the whole fetch succeeded, so a half-written
    # fixture can never be committed.
    tape.save()
    return result


def _make_tape(games: list[ScheduledGame], *, record: Path | None, replay: Path | None):
    """Pick the transport. Recording and replaying are mutually exclusive."""
    if record and replay:
        raise ValueError("Cannot record and replay odds in the same run.")

    if replay:
        return ReplayTape(replay)

    if record:
        first = games[0] if games else None
        return RecordingTape(
            record,
            meta={
                "season": first.season if first else None,
                "week": first.week if first else None,
                "book": config.ODDS_BOOK,
            },
        )

    return LiveTape()


# ---------------------------------------------------------------------------
# The Odds API
# ---------------------------------------------------------------------------


def _from_api(games: list[ScheduledGame], tape=None) -> OddsFetchResult:
    tape = tape or LiveTape()

    # Replay serves recorded responses, so it needs no credential.
    key = ""
    if not getattr(tape, "replaying", False):
        try:
            key = config.odds_api_key()
        except RuntimeError as exc:
            raise OddsUnavailable(str(exc)) from exc

    book = config.ODDS_BOOK
    warnings: list[str] = []

    with httpx.Client(
        timeout=config.HTTP_TIMEOUT,
        headers={"User-Agent": config.USER_AGENT},
    ) as client:
        events, quota = _get_bulk(client, key, book, tape)
        by_game = _match_events(events, games)

        check_match_rate(tape, len(by_game), len(games), config.REPLAY_MIN_MATCH_RATE)
        if getattr(tape, "replaying", False):
            log.info(
                "Replay matched %d/%d games (fixture recorded %s).",
                len(by_game),
                len(games),
                getattr(tape, "recorded_at", "unknown"),
            )

        odds: dict[str, Odds] = {}
        for game in games:
            event = by_game.get(game.game_id)
            if event is None:
                warnings.append(f"{game.game_id}: no {book} event, using nflverse line.")
                odds[game.game_id] = _fallback_odds(game)
                continue
            odds[game.game_id] = _parse_event(event, game, book)

        if config.ODDS_FETCH_TEAM_TOTALS:
            # Prefer the later reading, including zero -- an exhausted quota is
            # exactly the number worth reporting.
            latest = _apply_team_totals(client, key, book, by_game, odds, warnings, tape)
            if latest is not None:
                quota = latest

    # Anything the market did not post, derive.
    for game in games:
        _ensure_team_totals(odds[game.game_id], game)

    if quota is not None and quota < config.ODDS_QUOTA_FLOOR:
        warnings.append(f"Odds API quota low: {quota} credits remaining.")
    log.info("Odds API quota remaining: %s", quota)

    return OddsFetchResult(odds=odds, source="odds_api", quota_remaining=quota, warnings=warnings)


def _get_bulk(client: httpx.Client, key: str, book: str, tape) -> tuple[list[dict], int | None]:
    url = f"{config.ODDS_API_BASE}/sports/{config.ODDS_SPORT_KEY}/odds"
    params = {
        "apiKey": key,
        "markets": ",".join(config.ODDS_BULK_MARKETS),
        "oddsFormat": "american",
        "bookmakers": book,
    }

    response = tape.get(client, url, params)
    quota = _quota(response)

    if response.status_code == 401:
        raise OddsUnavailable("Odds API rejected the key (401).")
    if response.status_code == 429:
        raise OddsUnavailable("Odds API quota exhausted (429).")
    response.raise_for_status()

    return response.json(), quota


def _apply_team_totals(
    client: httpx.Client,
    key: str,
    book: str,
    by_game: dict[str, dict],
    odds: dict[str, Odds],
    warnings: list[str],
    tape,
) -> int | None:
    """Fetch the team_totals market one event at a time.

    A failure here is not fatal: team totals derive cleanly from the spread and
    total, so we log, warn, and move on rather than losing the whole build.
    """
    quota = None

    for game_id, event in by_game.items():
        event_id = event.get("id")
        if not event_id:
            continue

        url = f"{config.ODDS_API_BASE}/sports/{config.ODDS_SPORT_KEY}/events/{event_id}/odds"
        params = {
            "apiKey": key,
            "markets": "team_totals",
            "oddsFormat": "american",
            "bookmakers": book,
        }

        try:
            response = tape.get(client, url, params)
            reading = _quota(response)
            if reading is not None:
                quota = reading
            if response.status_code == 429:
                warnings.append("Odds API quota exhausted while fetching team totals.")
                break
            if response.status_code >= 400:
                continue
            _parse_team_totals(response.json(), odds[game_id], book)
        except httpx.HTTPError as exc:
            log.debug("team_totals fetch failed for %s: %s", game_id, exc)

    return quota


def _quota(response: httpx.Response) -> int | None:
    raw = response.headers.get("x-requests-remaining")
    try:
        return int(float(raw)) if raw is not None else None
    except ValueError:
        return None


def _match_events(events: list[dict], games: list[ScheduledGame]) -> dict[str, dict]:
    """Join odds events onto scheduled games by team pair and kickoff proximity.

    An event whose teams cannot be mapped is a hard error. An event that maps
    but matches no scheduled game is ignored -- that is just a game from
    another week sharing the response.
    """
    by_pair: dict[tuple[str, str], list[dict]] = {}

    for event in events:
        try:
            home = to_abbr(event["home_team"])
            away = to_abbr(event["away_team"])
        except (KeyError, UnmappedTeamError) as exc:
            raise UnmappedTeamError(
                f"Odds event {event.get('id')} could not be mapped: {exc}"
            ) from exc
        by_pair.setdefault((away, home), []).append(event)

    matched: dict[str, dict] = {}
    for game in games:
        for event in by_pair.get((game.away, game.home), []):
            commence = _parse_iso(event.get("commence_time"))
            if commence is None or abs(commence - game.kickoff_utc) <= _KICKOFF_TOLERANCE:
                matched[game.game_id] = event
                break

    return matched


def _parse_iso(value):
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_event(event: dict, game: ScheduledGame, book: str) -> Odds:
    odds = Odds(
        source="odds_api",
        book=book,
        book_label=config.book_label(book),
    )

    markets = _markets(event, book)

    for outcome in markets.get("spreads", []):
        point = outcome.get("point")
        if point is None:
            continue
        try:
            team = to_abbr(outcome["name"])
        except UnmappedTeamError:
            continue

        if team == game.home:
            odds.spread_price_home = _american(outcome.get("price"))
        elif team == game.away:
            odds.spread_price_away = _american(outcome.get("price"))

        # The favorite is whoever is laying points.
        if point < 0 and (odds.spread is None or point > odds.spread):
            odds.spread = float(point)
            odds.spread_favorite = team

    # Pick'em: both sides at zero, so nobody is laying anything.
    if odds.spread is None and markets.get("spreads"):
        points = {o.get("point") for o in markets["spreads"]}
        if points == {0} or points == {0.0}:
            odds.spread = 0.0
            odds.spread_favorite = game.home

    for outcome in markets.get("totals", []):
        if outcome.get("point") is None:
            continue
        odds.total = float(outcome["point"])
        if str(outcome.get("name", "")).lower() == "over":
            odds.over_price = _american(outcome.get("price"))
        else:
            odds.under_price = _american(outcome.get("price"))

    return odds


def _parse_team_totals(payload: dict, odds: Odds, book: str) -> None:
    """Read the posted team totals, taking the Over side as the number."""
    markets = _markets(payload, book)

    for outcome in markets.get("team_totals", []):
        point = outcome.get("point")
        description = outcome.get("description") or outcome.get("name")
        if point is None or not description:
            continue
        if str(outcome.get("name", "")).lower() not in {"over", ""}:
            continue

        try:
            team = to_abbr(description)
        except UnmappedTeamError:
            continue

        if team == _home_of(payload):
            odds.home_team_total = float(point)
        else:
            odds.away_team_total = float(point)

    if odds.home_team_total is not None or odds.away_team_total is not None:
        odds.team_totals_derived = False


def _home_of(payload: dict) -> str | None:
    try:
        return to_abbr(payload["home_team"])
    except (KeyError, UnmappedTeamError):
        return None


def _markets(event: dict, book: str) -> dict[str, list[dict]]:
    """Flatten one bookmaker's markets into {market_key: [outcomes]}."""
    for bookmaker in event.get("bookmakers", []):
        if bookmaker.get("key") != book:
            continue
        return {
            market["key"]: market.get("outcomes", [])
            for market in bookmaker.get("markets", [])
        }
    return {}


def _american(price) -> int | None:
    try:
        return int(price)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# nflverse fallback
# ---------------------------------------------------------------------------


def _fallback(games: list[ScheduledGame], reason: str) -> OddsFetchResult:
    odds = {}
    for game in games:
        odds[game.game_id] = _fallback_odds(game)
        _ensure_team_totals(odds[game.game_id], game)

    return OddsFetchResult(
        odds=odds,
        source="nflverse_fallback",
        warnings=[reason],
    )


def _fallback_odds(game: ScheduledGame) -> Odds:
    """Translate nflverse's home-relative spread into favorite-relative."""
    spread = None
    favorite = None

    if game.spread_line is not None:
        if game.spread_line == 0:
            spread, favorite = 0.0, game.home
        else:
            # nflverse spread_line is positive when the home team is favored.
            favorite = game.home if game.spread_line > 0 else game.away
            spread = -abs(game.spread_line)

    return Odds(
        source="nflverse_fallback",
        book=None,
        book_label="consensus (nflverse)",
        spread=spread,
        spread_favorite=favorite,
        total=game.total_line,
    )


def _ensure_team_totals(odds: Odds, game: ScheduledGame) -> None:
    """Derive team totals from the spread and total when no market is posted.

    The favorite's implied total is half the game total plus half the spread;
    the underdog's is half minus half. With total 45.5 and SEA -4.5 that gives
    SEA 25.0 and the opponent 20.5, matching how books price it.

    Each side is rounded to the half-point books actually post, so the two need
    not re-add to exactly the game total.
    """
    if odds.home_team_total is not None and odds.away_team_total is not None:
        return
    if odds.total is None or odds.spread is None or odds.spread_favorite is None:
        return

    # Points the home team is favored by: positive when home is the favorite,
    # negative when it is the underdog.
    home_edge = -odds.spread if odds.spread_favorite == game.home else odds.spread

    half_total = odds.total / 2
    half_edge = home_edge / 2

    if odds.home_team_total is None:
        odds.home_team_total = _round_half(half_total + half_edge)
    if odds.away_team_total is None:
        odds.away_team_total = _round_half(half_total - half_edge)

    odds.team_totals_derived = True


def _round_half(value: float) -> float:
    return round(value * 2) / 2
