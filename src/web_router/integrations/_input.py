"""The model's tool input, checked against the tool's schema before anything is sent."""

from __future__ import annotations

from typing import Any

from web_router.constants import INTEGRATION_EXTRACT_MAX_URLS, MSG_INTEGRATION_BAD_INPUT


def invalid(method: str, name: str, tool_input: dict[str, Any]) -> str | None:
    """What the model reads when its input breaks the tool's schema; ``None`` when it fits."""
    if method in ("search", "answer"):
        query = tool_input.get("query")
        ok = isinstance(query, str) and bool(query.strip())
    else:
        urls = tool_input.get("urls")
        ok = (
            isinstance(urls, list)
            and 1 <= len(urls) <= INTEGRATION_EXTRACT_MAX_URLS
            and all(isinstance(url, str) and url for url in urls)
        )
    return None if ok else MSG_INTEGRATION_BAD_INPUT[method].format(tool=name, max_urls=INTEGRATION_EXTRACT_MAX_URLS)
