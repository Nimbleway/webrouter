"""Extract's constants: what it reads as a page that is not the page asked for, and the MCP extract tool's schema.

Kept apart from :mod:`web_router.constants`, so that file stops growing; it
imports the extract tool's tables from here.
"""

from __future__ import annotations

import re
from typing import Any, Final, get_args

from web_router.constants_search import described
from web_router.types import ExtractFormat, ExtractRequest

MSG_EXTRACT_CHALLENGE: Final = "the backend got a bot challenge instead of the page"
"""A Cloudflare "Just a moment..." page, a CAPTCHA, a "verify you are human" check."""

MSG_EXTRACT_LOGIN_WALL: Final = "the backend got a sign-in page instead of the page"
"""A login wall: the page shows its content only to a signed-in visitor."""

MSG_EXTRACT_RETRY_BLANK: Final = "[{provider}] returned a blank page"
"""Why a URL ``auto`` retried stays failed when the later member read it as a page with no text."""

MSG_EXTRACT_FETCHED_NOTHING: Final = "the backend fetched nothing and reported status {status!r}"
"""A backend's own fetch state saying it got no page, as Nimble's ``status`` does: no page came back."""

MSG_EXTRACT_NEAR_EMPTY: Final = (
    "the page has almost no text: it may be a shell its scripts fill in, or a placeholder rather than the page"
)
"""The warning on a document with under :data:`EXTRACT_NEAR_EMPTY_CHARS` characters of text."""

EXTRACT_NEAR_EMPTY_CHARS: Final = 64
"""Below this many characters of text a page is near-empty: kept, with a warning.

A script-rendered shell comes back as a few dozen characters. Some real
answers are that short too -- a plain-text endpoint, a one-line JSON reply --
which is why such a page stays a document rather than a failure.
"""

EXTRACT_WALL_MAX_CHARS: Final = 3000
"""Text markers count only on a page shorter than this; an article that mentions a challenge page is not one."""

EXTRACT_CHALLENGE_TITLE: Final = re.compile(
    r"^\s*(?:just a moment|attention required|access denied|security check|are you a robot|"
    r"verify(?:ing)? you are (?:a )?human|one more step|pardon our interruption|captcha)\b"
    r"[\s.\u2026!?]*(?:$|[|\-\u2013\u2014:\u00b7])",
    re.IGNORECASE,
)
"""A challenge page's title, alone or before a separator: ``Just a moment...``, ``Attention Required! | Cloudflare``."""

EXTRACT_LOGIN_TITLE: Final = re.compile(
    r"^\s*(?:\w+\s+)?(?:sign in|sign-in|signin|log in|log-in|login|sign up)\b"
    r"[\s.\u2026!?]*(?:$|[|\-\u2013\u2014:\u00b7,])",
    re.IGNORECASE,
)
"""A sign-in page's title, alone or before a separator, after at most one word.

``Sign In | LinkedIn``, ``LinkedIn Login, Sign in``, ``Login - Example`` match.

``Sign in with Apple: a guide`` does not match: a word follows the phrase, not a separator.
"""

EXTRACT_CHALLENGE_MARKERS: Final = (
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
    "verify you are human",
    "verifying you are human",
    "complete the security check",
    "cf-browser-verification",
    "cf_chl_",
    "please solve the captcha",
    "press & hold to confirm you are",
)
"""Phrases of a challenge page's text, matched case-blind on a page shorter than :data:`EXTRACT_WALL_MAX_CHARS`."""

EXTRACT_LOGIN_MARKERS: Final = (
    "sign in to continue",
    "sign in to view",
    "log in to continue",
    "login to continue",
    "log in to view",
    "you need to sign in",
    "join to view",
)
"""Phrases of a login wall's text, matched like :data:`EXTRACT_CHALLENGE_MARKERS`.

Only phrases that gate the page itself: "you must be logged in" and "please log in to" end the comment form of
many a short post, so they are not here.
"""

EXTRACT_LOGIN_URL_PARTS: Final = ("/authwall",)
"""How the path a login wall redirects to begins, whatever the page's length: LinkedIn's is ``/authwall``."""

HTML_TITLE: Final = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
"""An HTML body's ``<title>``, read when a backend returns HTML with no title of its own.

Markers in the body are not read on a long HTML page: a protected site's own pages carry the challenge scripts too.
"""

# `profile` is left out on purpose, as on `browser_open`: a saved, logged-in
# identity is the user's own, set in code, never from a conversation.
MCP_EXTRACT_PROPERTIES: dict[str, dict[str, Any]] = {
    "urls": {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "description": described(ExtractRequest, "urls"),
    },
    "format": {
        "type": "string",
        "enum": list(get_args(ExtractFormat)),
        "description": "A backend that cannot produce it refuses rather than return another.",
    },
    "data_apis": {
        "type": "boolean",
        "description": "Also list each page's site's vendor data APIs; free, never run. On by default.",
    },
    "configured_only": {"type": "boolean", "description": "Only data APIs whose credentials are set here."},
}

# A tool description is the routing decision: the model choosing between the tools is the actual router
# (`web_run` hands it to ours), and latency and cost are what it chooses on.
MCP_DESC_EXTRACT = (
    "Read pages you have URLs for, as markdown, HTML or text, in a few seconds. It reads, it does not "
    "find: URLs come from `search`, the user or a page. A page behind a login or clicks is "
    "`browser_open`'s. provider='max' races every backend, one fetch each: only when the user wants speed "
    "over cost. `data_apis`: its site's data APIs, for `call_data_api`."
)
