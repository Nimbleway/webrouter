"""Pinning a browse mode: each declared part sends its wire beside the task and the start page, a caller's options
survive or are refused, and the job a pinned backend hands back keeps its mode.

Every vendor is mocked at its HTTP transport, so these read the body each backend would send.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from scripts.contracts_modes import parts
from web_router import Budget, WebRouter
from web_router.exceptions_budget import BudgetExceededError
from web_router.exceptions_typed import InvalidParameterError
from web_router.providers.base import BrowseJob
from web_router.registry import create_browse_provider, get_browse_provider_class
from web_router.routing.candidates import build_candidates
from web_router.types import BrowseRequest

DECLARING = ("browser-use", "skyvern")
PINS = [(name, part) for name in DECLARING for part in parts(get_browse_provider_class(name))]
TASK = "Find the top story"
START = "https://news.example/"
CALLER = {"caller_key": 1}
"""A ``provider_options`` key no mode writes."""

STARTED = {"id": "task-1", "run_id": "run-1", "status": "completed", "output": "Done."}
"""What either vendor's start call answers, as far as each reads it: Skyvern's run finished at once."""


def recording(bodies: list[dict[str, Any]]) -> httpx.MockTransport:
    """A transport that records each JSON body posted and answers :data:`STARTED`."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            bodies.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json=STARTED)

    return httpx.MockTransport(handle)


def sent(pin: str, request: BrowseRequest) -> dict[str, Any]:
    """The one body ``pin`` starts ``request`` with."""
    bodies: list[dict[str, Any]] = []
    create_browse_provider(pin, api_key="k", transport=recording(bodies)).submit(request)
    assert len(bodies) == 1, "one call to start the task"
    return bodies[0]


@pytest.mark.parametrize(("name", "part"), PINS, ids=[f"{n}:{p}" for n, p in PINS])
def test_pinning_a_part_sends_exactly_its_wire_beside_the_task_and_the_callers_options(name: str, part: str) -> None:
    request = BrowseRequest(task=TASK, start_url=START, provider_options=CALLER)
    plain = sent(name, request)
    pinned = sent(f"{name}:{part}", request)

    wire = dict(get_browse_provider_class(name).tiers[part].wire)
    assert {field: value for field, value in pinned.items() if plain.get(field) != value} == wire
    assert pinned.items() >= CALLER.items()
    assert {TASK, START} <= set(pinned.values()), "the task and the start page stay the router's"


@pytest.mark.parametrize(
    ("pin", "options", "named"),
    [
        ("browser-use:browser-use-o3", {"provider_options": {"llm": "browser-use-2.0"}}, "provider_options"),
        ("skyvern:skyvern-2.0", {"provider_options": {"engine": "skyvern-1.0"}}, "provider_options"),
        ("skyvern:skyvern-2.0+lite", {}, "no option for"),
    ],
)
def test_a_mode_the_caller_overrides_or_cannot_take_is_refused_before_anything_is_sent(
    pin: str, options: dict[str, Any], named: str
) -> None:
    bodies: list[dict[str, Any]] = []
    with pytest.raises(InvalidParameterError, match=named):
        create_browse_provider(pin, api_key="k", transport=recording(bodies)).submit(
            BrowseRequest(task=TASK, **options)
        )
    assert bodies == []


def test_a_pin_that_names_another_method_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="pinned browse backend"):
        get_browse_provider_class("answer:skyvern:skyvern-2.0")


def test_a_pinned_backend_hands_back_its_job_which_states_no_cost_and_answers() -> None:
    provider = create_browse_provider("skyvern:skyvern-2.0", api_key="k", transport=recording([]))
    job = provider.submit(BrowseRequest(task=TASK))
    assert isinstance(job, BrowseJob)
    assert job.result().answer == "Done."


@pytest.mark.parametrize(("cap", "offered"), [(1.0, []), (None, ["skyvern:skyvern-2.0", "browser-use:browser-use-o3"])])
def test_an_unpriced_tier_is_offered_only_without_a_cost_cap(
    monkeypatch: pytest.MonkeyPatch, cap: float | None, offered: list[str]
) -> None:
    monkeypatch.setenv("SKYVERN_API_KEY", "k")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "k")
    chain = {"browse": ["skyvern:skyvern-2.0", "browser-use:browser-use-o3"]}
    router = WebRouter(provider_config={"auto": {"chain": chain}})
    budget = None if cap is None else Budget(max_cost_usd=cap)
    found = build_candidates(router, {"browse": BrowseRequest(task=TASK)}, budget=budget)
    assert [candidate.provider for candidate in found.candidates] == offered


def test_a_named_unpriced_pin_never_sends_under_a_cost_cap() -> None:
    bodies: list[dict[str, Any]] = []
    router = WebRouter(provider_config={"skyvern": {"api_key": "k", "transport": recording(bodies)}})
    with pytest.raises(BudgetExceededError):
        router.browse(TASK, provider="skyvern:skyvern-2.0", budget=Budget(max_cost_usd=1.0))
    assert bodies == []
