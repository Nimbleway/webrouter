"""Browserbase: its host, auth header, sessions, browse runs and Fetch extract.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Browserbase's variable and its rows
in the method tables stay there, beside every other backend's.
"""

from __future__ import annotations

from typing import Any

from web_router.types import (
    REQUEST_VALUE,
    BrowserField,
    BrowserFieldRow,
    BrowserState,
    ExtractFormat,
    JobState,
    StealthLevel,
)

# --- Browserbase browser ----------------------------------------------------
# From Browserbase's API reference and its stealth / proxies guides, not a live
# call: no account was available as of 2026-09-24. See
# tests/providers/test_browserbase_browser.py, and its docstring for what these
# checks found.

BROWSERBASE_BASE_URL = "https://api.browserbase.com"
BROWSERBASE_OPEN_PATH = "/v1/sessions"
BROWSERBASE_SESSION_PATH = "/v1/sessions/{session_id}"
"""Reads a session on ``GET`` and, on ``POST``, updates it -- which is how one is released."""

BROWSERBASE_API_KEY_HEADER = "X-BB-API-Key"

BROWSERBASE_STEALTH: dict[StealthLevel, dict[tuple[str, ...], Any]] = {
    # The reference deprecates `browserSettings.advancedStealth` in favor of
    # `browserSettings.verified` -- same boolean, plan-gated: on 2026-10-29 a
    # live open with `high` answered 403 "Verified mode is only available on
    # the Enterprise plan", so a lower plan gets that error, never `low` -- so
    # `verified` is what these levels set rather than the field the original
    # spec named. Sending `false` for `low` says "the ordinary session" rather
    # than leaving the option unread, matching the row for `high`. The
    # reference gives no field for turning a session's baseline evasion off,
    # so `off` has no row and is refused by `stealth_levels` below.
    "low": {("browserSettings", "verified"): False},
    "high": {("browserSettings", "verified"): True},
}
"""Stealth level -> the field it sets in Browserbase's body. Unverified -- see the module docstring."""

BROWSERBASE_FIELDS: dict[str, BrowserFieldRow] = {
    "timeout": ("timeout",),
    "stealth": BROWSERBASE_STEALTH,
    "solve_captchas": ("browserSettings", "solveCaptchas"),
    "proxy_country": BrowserField(
        {("proxies",): [{"type": "browserbase", "geolocation": {"country": REQUEST_VALUE}}]},
        case="upper",
    ),
    "profile": ("browserSettings", "context", "id"),
    "persist_profile": ("browserSettings", "context", "persist"),
}
"""Normalized option -> where it goes in Browserbase's body.

``profile`` is a context id and ``persist_profile`` its ``persist`` flag, both
checked live on 2026-10-20: a cookie and a localStorage entry set in a session
opened with ``persist`` were there in the next session opened on the context.
The other rows are unverified -- see the module docstring.
"""

BROWSERBASE_DEBUG_PATH = "/v1/sessions/{session_id}/debug"
"""Answers a running session's live-view links. Checked live on 2026-10-20."""

BROWSERBASE_CONTROL_URL_FIELD = "debuggerFullscreenUrl"
"""The debug answer's link to the session's page alone, without Chrome's DevTools around it."""

BROWSERBASE_RELEASE_BODY: dict[str, str] = {"status": "REQUEST_RELEASE"}
"""Ends a session: Browserbase has no release call, only an update asking for one."""

BROWSERBASE_SESSION_STATES: dict[str, BrowserState] = {
    "PENDING": "starting",
    "RUNNING": "running",
    "COMPLETED": "released",
    "TIMED_OUT": "expired",
    "ERROR": "failed",
}
"""Browserbase's session ``status`` -> the normalized state."""

# --- Browserbase browse -----------------------------------------------------
# From Browserbase's Agents API reference, checked against live prose runs on
# 2026-09-30 and 2026-10-21; ``resultSchema`` has not had one. See
# tests/providers/test_browserbase_browse.py. The base URL, key header and env
# var are the session provider's above.

BROWSERBASE_BROWSE_START_PATH = "/v1/agents/runs"
"""Starts a run. With no ``agentId`` it also creates a new Agent in the account for that run."""

BROWSERBASE_BROWSE_RUN_PATH = "/v1/agents/runs/{job_id}"
BROWSERBASE_BROWSE_STOP_PATH = "/v1/agents/runs/{job_id}/stop"
"""Answers 202 while the run winds down, and 409 for a run that has already finished."""

BROWSERBASE_RESULT_SCHEMA_FIELD = "resultSchema"
"""The request field that asks for a result conforming to a schema -- the one case its ``result`` is ``structured``."""

BROWSERBASE_RESULT_SUMMARY_FIELD = "summary"
"""Where a run with no schema puts its answer.

Verified live on 2026-09-30: a run with no ``resultSchema`` came back with an
object ``result`` -- ``{"summary": "...", "stepsTaken": 2, "taskDuration":
3756}`` -- not the prose string the docs describe, so the answer is its summary
and the object is not reported as structured output nobody asked for.
"""

BROWSERBASE_BROWSE_STATES: dict[str, JobState] = {
    "PENDING": "queued",
    "RUNNING": "running",
    "COMPLETED": "completed",
    "FAILED": "failed",
    # "Exceeded maximum time": Browserbase's own limit, with no sign of how far
    # the agent got, so a failure rather than a partial answer.
    "TIMED_OUT": "failed",
    "STOPPED": "cancelled",
}
"""Browserbase's run ``status`` -> the normalized state."""

# --- Browserbase extract ----------------------------------------------------
# From https://docs.browserbase.com/features/fetch and
# https://www.browserbase.com/blog/introducing-authenticated-fetch, confirmed
# live on 2026-10-29 in both formats and with a Context -- see
# tests/providers/test_browserbase_extract.py. Fetch is a plain HTTP fetch --
# it never renders the page in a browser -- that can send the cookies a Context
# saved, which is what makes a page behind a login reachable without a session.

BROWSERBASE_EXTRACT_PATH = "/v1/fetch"
"""One URL per call; the base class fans a request out."""

BROWSERBASE_EXTRACT_FORMATS: dict[ExtractFormat, str] = {
    # `raw` is the body as the target served it, which for a page is its HTML;
    # `contentType` in the document's `extra` says what it really was. Fetch's
    # third format, `json`, needs a schema and is not an extract format.
    "html": "raw",
    "markdown": "markdown",
}
"""Normalized format -> Fetch's ``format`` field."""

BROWSERBASE_EXTRACT_DEFAULT_FORMAT: ExtractFormat = "html"
"""Fetch returns ``raw`` when asked for nothing in particular."""

BROWSERBASE_PROFILE_FIELD = "contextId"
"""The Fetch field that names the Context whose saved cookies ride along with the request."""

BROWSERBASE_FETCH_BINARY_ENCODING = "base64"
"""Fetch's ``encoding`` for a body it could not return as text -- a PDF, an image."""

BROWSERBASE_HTML_CONTENT_TYPES: tuple[str, ...] = ("text/html", "application/xhtml")
"""``contentType`` prefixes that are a page's HTML, so a ``raw`` body may be labelled ``html``."""

MSG_BROWSERBASE_FETCH_NO_TEXT = "Fetch returned no text content (got {kind})"
"""A 200 whose ``content`` is missing or structured -- Fetch's ``json`` format -- is not a page's text."""

MSG_BROWSERBASE_FETCH_NOT_FORMAT = "Fetch returned {content_type} ({encoding}), not {format}"
"""A body that is binary, or not HTML when HTML was asked for, is reported rather than labelled as the format."""
