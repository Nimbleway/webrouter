"""What the pinned-mode tests share: a transport that records each body sent, and the one body a pin sends.

Every vendor is mocked at its HTTP transport, so a test reads the body each backend would send.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from typing import Any

import httpx

from web_router.exceptions import WebRouterError
from web_router.registry import create_browse_provider
from web_router.registry_answer import create_answer_provider

CALLS = {"extract": "aextract", "answer": "aanswer", "browse": "asubmit"}
"""The async call each method's provider makes, the one a pinned class stamps its mode on."""


def recording(bodies: list[dict[str, Any]], reply: dict[str, Any] | None = None) -> httpx.MockTransport:
    """A transport that records the JSON body of each POST and answers every request with ``reply``."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            bodies.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json=reply or {})

    return httpx.MockTransport(handle)


def created(method: str) -> Callable[[str, httpx.MockTransport], Any]:
    """How ``method``'s backends are built on a mock transport, for those that take one as a keyword."""
    create = {"answer": create_answer_provider, "browse": create_browse_provider}[method]
    return lambda pin, transport: create(pin, api_key="k", transport=transport)


async def sent(
    method: str,
    pin: str,
    request: Any,
    build: Callable[[str, httpx.MockTransport], Any] | None = None,
    reply: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The one body ``pin`` sends for ``request``; an empty ``reply`` may fail the call, which is not the point."""
    bodies: list[dict[str, Any]] = []
    provider = (build or created(method))(pin, recording(bodies, reply))
    with contextlib.suppress(WebRouterError):
        await getattr(provider, CALLS[method])(request)
    assert len(bodies) == 1, "one call to the vendor"
    return bodies[0]
