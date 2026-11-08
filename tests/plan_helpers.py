"""What the ``plan()`` and ``run()`` tests share: the prompt, each method's planned params, and the members."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.routing_helpers import MEMBERS, TOOLS
from tests.routing_helpers import member as _member
from web_router import RouteDecision, RouteRequest
from web_router.types import BrowseResponse, ExtractResponse, ResearchResponse, SearchResponse
from web_router.types_answer import AnswerResponse

URL = "https://example.com/report"
PROMPT = f"what does {URL} say about 2024?"
RESPONSE = {
    "search": SearchResponse,
    "extract": ExtractResponse,
    "answer": AnswerResponse,
    "research": ResearchResponse,
    "browse": BrowseResponse,
}
PARAMS = {
    "search": {"query": PROMPT},
    "extract": {"urls": [URL]},
    "answer": {"query": PROMPT},
    "research": {"query": PROMPT},
    "browse": {"task": PROMPT, "start_url": URL},
}


@pytest.fixture(name="called")
def called_fixture() -> Iterator[list[str]]:
    """``first``, ``second`` and ``third`` registered for every method; what was called, as ``tool:member``."""
    log: list[str] = []
    for tool in TOOLS:
        log_for = _Tagged(log, tool.name)
        for name in MEMBERS:
            tool.register(name, _member(tool, name, log_for, fails=name != "third"))
    yield log
    for tool in TOOLS:
        for name in MEMBERS:
            tool.unregister(name)


class _Tagged(list):  # type: ignore[type-arg]
    """A log that records each member as ``tool:member`` in the shared one."""

    def __init__(self, shared: list[str], tool: str) -> None:
        super().__init__()
        self.shared, self.tool = shared, tool

    def append(self, name: str) -> None:  # type: ignore[override]
        self.shared.append(f"{self.tool}:{name}")


def chooser(tool: str | None, **decision: Any) -> Any:
    """A router that picks ``tool``, ranks ``third`` first for it, and keeps what it was shown."""

    def route(req: RouteRequest) -> RouteDecision:
        route.seen.append(req)  # type: ignore[attr-defined]
        tools = None if tool is None else [tool]
        return RouteDecision(tools=tools, providers={tool: ["third"]} if tool else None, reason="picked", **decision)

    route.seen = []  # type: ignore[attr-defined]
    return route
