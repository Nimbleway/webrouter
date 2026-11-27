"""Context Search's fixed endpoint, count bound and safe failure message."""

from __future__ import annotations

CONTEXT_BASE_URL = "https://api.context.dev"
CONTEXT_SEARCH_PATH = "/v1/web/search"
CONTEXT_API_KEY_PREFIX = "Bearer "
CONTEXT_MAX_RESULTS = 100
CONTEXT_HIGHLIGHTS_SUCCESS = "SUCCESS"
CONTEXT_HIGHLIGHTS_NOT_REQUESTED = "NOT_REQUESTED"
CONTEXT_HIGHLIGHTS_CODES = frozenset(
    {
        CONTEXT_HIGHLIGHTS_SUCCESS,
        CONTEXT_HIGHLIGHTS_NOT_REQUESTED,
        "TIMEOUT",
        "CONTENT_TOO_LARGE",
        "WEBSITE_ACCESS_ERROR",
        "WEBSITE_BLOCKED",
        "WEBSITE_NOT_FOUND",
        "ERROR",
    }
)
MSG_CONTEXT_FAILED = "Context.dev search could not complete the request."
