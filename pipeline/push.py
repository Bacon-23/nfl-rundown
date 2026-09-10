"""Push a built week to WordPress.

The endpoint only accepts stats. It cannot create or edit posts, so a bad push
can never damage published editorial -- the worst case is stale numbers on an
unpublished week.
"""

from __future__ import annotations

import logging
import sys
import time

import httpx

from pipeline import config
from pipeline.schema import WeekPayload

log = logging.getLogger(__name__)


class PushError(RuntimeError):
    pass


class TransientPushError(PushError):
    """A failure worth repeating: the site had a bad moment, not a bad request.

    Kept a subclass of PushError so every existing `except PushError` still
    catches it; the distinction exists only so the retry loop can tell a 502
    from a rejected token. Without it the loop retries a 403 three times and
    buries the one message that tells the operator what to fix.
    """


def endpoint(path: str) -> str:
    """Build a route URL, refusing to name a host the token must not reach.

    The scheme is checked here rather than left to the endpoint, because the
    bearer token travels with the request: WordPress rejects a cleartext call
    (`is_ssl()` in rest-ingest.php), but only once the credential has already
    crossed the wire. httpx does not follow redirects by default, so an http://
    value does not silently upgrade either -- it just leaks and then fails.

    Distinct per-environment tokens already make a WP_SITE_URL pointing at the
    wrong site fail loudly. This is the other half: a WP_SITE_URL pointing
    somewhere unencrypted fails before anything is sent.
    """
    if not config.WP_SITE_URL:
        raise PushError("WP_SITE_URL is not set.")

    if not config.WP_SITE_URL.lower().startswith("https://"):
        raise PushError(
            f"WP_SITE_URL must start with https:// -- got {config.WP_SITE_URL!r}. "
            "The bearer token is sent with the request, so it is not sent at all "
            "until the destination is encrypted."
        )

    return f"{config.WP_SITE_URL}/wp-json/{config.WP_REST_NAMESPACE}/{path.lstrip('/')}"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.wp_token()}",
        "Content-Type": "application/json",
        "User-Agent": config.USER_AGENT,
    }


def _with_retries(what: str, attempt_once):
    """Run `attempt_once`, retrying transient failures with a backoff.

    Retries only on network errors and 5xx. A 401, 403, or 422 means the
    request itself is wrong, and repeating it will not help -- it only delays
    the message the operator actually needs.
    """
    last_error: Exception | None = None

    for attempt in range(1, config.HTTP_RETRIES + 1):
        try:
            return attempt_once()
        except (httpx.HTTPError, TransientPushError) as exc:
            last_error = exc
            if attempt == config.HTTP_RETRIES:
                break
            delay = 2 ** attempt
            log.warning(
                "%s attempt %d failed (%s); retrying in %ds.", what, attempt, exc, delay
            )
            time.sleep(delay)

    raise PushError(f"{what} failed after {config.HTTP_RETRIES} attempts: {last_error}")


def health() -> dict:
    """Verify credentials and that the table exists, without writing.

    Retries on the same terms as a push. This is a hard gate in CI -- a build
    that cannot reach the site stops before spending Odds API credits -- so a
    single dropped connection used to cost the entire run. One did, at 02:45
    UTC on 2026-09-08, with green builds on either side of it.
    """

    def once() -> dict:
        with httpx.Client(timeout=config.HTTP_TIMEOUT) as client:
            response = client.get(endpoint("health"), headers=_headers())

            # 5xx is the site having a bad moment; raising here routes it into
            # the retry rather than out to the caller as a final answer.
            if response.status_code >= 500:
                raise TransientPushError(
                    f"WordPress returned {response.status_code}: {response.text[:300]}"
                )

            _raise_for_status(response)
            return response.json()

    return _with_retries("Health probe", once)


def push_week(payload: WeekPayload) -> dict:
    """POST a week, retrying transient failures with a backoff.

    Retries only on network errors and 5xx. A 401, 403, or 422 means the
    request itself is wrong, and repeating it will not help.
    """
    body = payload.wire()
    url = endpoint("week")

    last_error: Exception | None = None

    with httpx.Client(timeout=config.HTTP_TIMEOUT) as client:
        for attempt in range(1, config.HTTP_RETRIES + 1):
            try:
                response = client.post(url, headers=_headers(), json=body)

                if response.status_code >= 500:
                    raise TransientPushError(
                        f"WordPress returned {response.status_code}: {response.text[:300]}"
                    )

                _raise_for_status(response)
                result = response.json()
                log.info(
                    "Pushed %s week %s: %s inserted, %s updated, %s openers.",
                    payload.season,
                    payload.week,
                    result.get("inserted"),
                    result.get("updated"),
                    result.get("openers_set"),
                )
                if result.get("skipped"):
                    log.warning(
                        "WordPress skipped %d games: %s",
                        len(result["skipped"]),
                        result["skipped"],
                    )
                return result

            except (httpx.HTTPError, TransientPushError) as exc:
                last_error = exc
                if attempt == config.HTTP_RETRIES:
                    break
                delay = 2 ** attempt
                log.warning("Push attempt %d failed (%s); retrying in %ds.", attempt, exc, delay)
                time.sleep(delay)

    raise PushError(f"Push failed after {config.HTTP_RETRIES} attempts: {last_error}")


def main(argv: list[str] | None = None) -> int:
    """`python -m pipeline.push --health` — the day-one reachability probe.

    Run from CI rather than a laptop, because the question is specifically
    whether GitHub Actions can reach the site. A WordPress.com staging site is
    noindexed, and whether it answers anonymous requests decides whether the
    REST ingest path works at all.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="push")
    parser.add_argument(
        "--health",
        action="store_true",
        help="Check credentials and that the table exists. Writes nothing.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not args.health:
        parser.error("Nothing to do. Pass --health, or use build_week --push.")

    try:
        result = health()
    except PushError as exc:
        print(f"UNREACHABLE: {exc}", file=sys.stderr)
        print(
            "\nIf this is a 401/403 the token is wrong. If it is a 404 the plugin "
            "is not active. If the request never completes, the site may not "
            "answer anonymous requests -- switch to the SSH transport.",
            file=sys.stderr,
        )
        return 1

    print(f"OK  {config.WP_SITE_URL}")
    for key, value in result.items():
        print(f"    {key}: {value}")

    return 0 if result.get("ok") else 1


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return

    hints = {
        401: "Missing or malformed bearer token.",
        403: "Token rejected. Check TRINITY_RUNDOWN_TOKEN matches wp-config.php.",
        404: "Route not found. Is the Trinity Rundown plugin active?",
        503: "TRINITY_RUNDOWN_TOKEN is not defined in wp-config.php.",
    }
    hint = hints.get(response.status_code, "")

    raise PushError(
        f"{response.status_code} from {response.request.url}. {hint} "
        f"Body: {response.text[:300]}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
