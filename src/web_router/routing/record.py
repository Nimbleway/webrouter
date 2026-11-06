"""What the decision record says of the call itself: its id, the prompt's hash, and a failure's code.

Experimental. Every routed or budgeted call's record is stamped here with a
fresh ``decision_id`` and the prompt's ``prompt_sha256``, never the prompt;
and a member that failed is recorded by :func:`error_code`, never by its
message, which may quote the query or a URL.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from web_router.routing.contract import ToolName


def prompt_of(tool: ToolName, request: Any) -> tuple[str, tuple[str, ...]]:
    """What a router reads for ``request``: the query or task, or for extract the URLs one per line; and its URLs."""
    if tool == "extract":
        return "\n".join(request.urls), tuple(request.urls)
    if tool == "browse":
        return request.task, (request.start_url,) if request.start_url else ()
    return request.query, ()


def stamp(prompt: str) -> dict[str, Any]:
    """A new record's ``decision_id`` and ``prompt_sha256``: an id unique to this decision, and the prompt's hash."""
    return {"decision_id": uuid.uuid4().hex, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}


def error_code(exc: BaseException) -> str:
    """A failure as a hop records it: the HTTP status when the backend answered with one, else the error's class."""
    original = getattr(exc, "original", None)
    for source in (getattr(original, "response", None), original, exc):
        status = getattr(source, "status_code", None)
        if isinstance(status, int) and not isinstance(status, bool):
            return str(status)
    return type(exc).__name__
