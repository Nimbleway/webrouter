"""What the ``fallback`` router tests share: members that answer, fail in each way, or take their time."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from web_router import RouteDecision, RouteRequest


def answers(label: str, log: list[str] | None = None, **attrs: Any) -> Any:
    """A sync member that decides search, with ``label`` as its reason; ``attrs`` set on it (``timeout_s`` ...)."""

    def member(req: RouteRequest) -> RouteDecision:
        if log is not None:
            log.append(label)
        return RouteDecision(tools=["search"], reason=label)

    for name, value in attrs.items():
        setattr(member, name, value)
    return member


def aanswers(label: str, log: list[str] | None = None, **attrs: Any) -> Any:
    """:func:`answers`, as a coroutine function."""

    async def member(req: RouteRequest) -> RouteDecision:
        if log is not None:
            log.append(label)
        return RouteDecision(tools=["search"], reason=label)

    for name, value in attrs.items():
        setattr(member, name, value)
    return member


def raises(req: RouteRequest) -> RouteDecision:
    raise RuntimeError("member down")


def invalid(req: RouteRequest) -> Any:
    return "search please"


def empty(req: RouteRequest) -> RouteDecision:
    return RouteDecision()


def slow(seconds: float, timeout_s: float, log: list[str] | None = None) -> Any:
    """A sync member that sleeps ``seconds`` before deciding, with ``timeout_s`` declared."""

    def member(req: RouteRequest) -> RouteDecision:
        if log is not None:
            log.append("slow")
        time.sleep(seconds)
        return RouteDecision(tools=["search"], reason="slow")

    member.timeout_s = timeout_s  # type: ignore[attr-defined]
    return member


def aslow(seconds: float, timeout_s: float) -> Any:
    """:func:`slow`, as a coroutine function."""

    async def member(req: RouteRequest) -> RouteDecision:
        await asyncio.sleep(seconds)
        return RouteDecision(tools=["search"], reason="slow")

    member.timeout_s = timeout_s  # type: ignore[attr-defined]
    return member


def request(deadline_s: float = 1.0, pinned_tool: Any = None) -> RouteRequest:
    """A request with no candidates, asked of a router directly."""
    return RouteRequest(prompt="who won", candidates=(), deadline_s=deadline_s, pinned_tool=pinned_tool)


def outcomes(decision: Any) -> list[tuple[str, str, bool]]:
    """Each member a decision lists: its name, its outcome and whether it counted."""
    return [(m.name, m.outcome, m.counted) for m in decision.members]
