"""A golden record of how every ``auto`` chain walks its members, through ``plan()`` and ``run()``.

Each routable method runs over ``first``, ``second`` and ``third``, a router
ranking ``second`` first: ``second`` fails before sending anything, ``third``
answers, and ``first`` is the case -- failing the same way, cooling down after
a 429, or failing ``local``, which stops the chain. The record is what a
change to the attempt loop must keep: the members offered, the merged order,
who was called in which order, the hops and attempts the decision records, and
what raised. The one-call chains (search, extract, answer) pass over a cooling
member without calling it; the job chains (research, browse) do not consult
cooldowns.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, nullcontext
from typing import Any

import pytest

from tests.plan_helpers import PROMPT
from tests.routing_helpers import MEMBERS, Ranker, Tool, member, wr
from web_router import AsyncWebRouter, WebRouter
from web_router.exceptions import ProviderError
from web_router.providers._runtime import Runtime, running
from web_router.types import ExtractResponse
from web_router.types_errors import ERROR_KIND_LOCAL, ERROR_KIND_RATE_LIMIT, ExtractFailure

ONE_CALL = {"search", "extract", "answer"}


def _stopping(tool: Tool, log: list[str]) -> type:
    """``first`` for ``tool``, failing ``local``: a failure every member would share, so the chain stops."""

    def entry(self: Any, *args: Any) -> Any:
        log.append("first")
        raise ProviderError("first", "the provider's own bug", kind=ERROR_KIND_LOCAL)

    return type("Member_first_local", (tool.fake,), {"name": "first", tool.entry: entry})


@pytest.fixture
def log(tool: Tool, case: str) -> Iterator[list[str]]:
    """The three members for ``tool``, ``first`` as ``case`` asks; yields who was called, in order."""
    called: list[str] = []
    for name in MEMBERS:
        built = member(tool, name, called, fails=name != "third")
        tool.register(name, _stopping(tool, called) if case == "stopped" and name == "first" else built)
    yield called
    for name in MEMBERS:
        tool.unregister(name)


def _lent(case: str) -> AbstractContextManager[None]:
    """For ``cooling``, a runtime lent to the run whose cooldowns hold ``first``, as a 429 it reported would."""
    if case != "cooling":
        return nullcontext()
    runtime = Runtime()
    throttled = ExtractFailure(url="https://a.test", error="429", status_code=429, kind=ERROR_KIND_RATE_LIMIT)
    runtime.cooldowns.note_answer("first", ExtractResponse(provider="first", failures=[throttled]))
    return running(runtime)


def _recorded(response: Any) -> dict[str, Any]:
    """The decision record, without what differs run to run: the id, the prompt's hash, timings."""
    record = response.decision
    return {
        "offered": record.offered,
        "ranked": record.ranked,
        "merged": record.merged,
        "ran": record.ran,
        "hops": [(hop.id, hop.error) for hop in record.hops or []],
        "attempts": [attempt.id for attempt in record.attempts or []],
        "reason": record.reason,
        "provider": response.provider,
    }


async def _traced(tool: Tool, case: str, asynchronous: bool, log: list[str]) -> dict[str, Any]:
    router = wr(AsyncWebRouter if asynchronous else WebRouter, router=Ranker("second"))
    plan = (await router.plan(PROMPT, tool=tool.name)) if asynchronous else router.plan(PROMPT, tool=tool.name)
    trace: dict[str, Any] = {"plan": (plan.tool, list(plan.providers[tool.name]), plan.decision.merged)}
    try:
        with _lent(case):
            response = (await router.run(plan)) if asynchronous else router.run(plan)
    except ProviderError as exc:
        trace["raised"] = (type(exc).__name__, exc.kind)
    else:
        trace["record"] = _recorded(response)
    trace["called"] = list(log)
    return trace


def _golden(tool: str, case: str) -> dict[str, Any]:
    """What ``tool`` did in ``case`` on the code before the attempt loop moved into one runner."""
    ids = [f"{tool}:{name}" for name in ("second", "first", "third")]
    trace: dict[str, Any] = {"plan": (tool, ["second", "first", "third"], ids)}
    if case == "stopped":
        return {**trace, "raised": ("ProviderError", ERROR_KIND_LOCAL), "called": ["second", "first"]}
    skipped = case == "cooling" and tool in ONE_CALL
    trace["record"] = {
        "offered": [f"{tool}:{name}" for name in MEMBERS],
        "ranked": [f"{tool}:second"],
        "merged": ids,
        "ran": 2,
        "hops": [(ids[0], "ConfigurationError"), (ids[1], "cooling_down" if skipped else "ConfigurationError")],
        "attempts": ids,
        "reason": "ranked",
        "provider": "third",
    }
    trace["called"] = ["second", "third"] if skipped else ["second", "first", "third"]
    return trace


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("case", ["ran", "cooling", "stopped"])
async def test_each_chain_walks_its_members_as_recorded(
    tool: Tool, case: str, asynchronous: bool, log: list[str]
) -> None:
    assert await _traced(tool, case, asynchronous, log) == _golden(tool.name, case)
