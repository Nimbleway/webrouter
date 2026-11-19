"""Pinning a browse mode: each declared part sends its wire beside the task and the start page, a caller's options
survive or are refused, and the job a pinned backend hands back keeps its mode.

Every vendor is mocked at its HTTP transport, so these read the body each backend would send.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.contracts_modes import parts
from tests.providers._pin_helpers import recording, sent
from web_router import Budget, WebRouter
from web_router.exceptions_budget import BudgetExceededError
from web_router.exceptions_typed import InvalidParameterError
from web_router.registry import create_browse_provider, get_browse_provider_class
from web_router.types import BrowseRequest

DECLARING = ("browser-use", "skyvern")
PINS = [(name, part) for name in DECLARING for part in parts(get_browse_provider_class(name))]
TASK = "Find the top story"
START = "https://news.example/"
CALLER = {"caller_key": 1}
"""A ``provider_options`` key no mode writes."""

STARTED = {"id": "task-1", "run_id": "run-1", "status": "completed", "output": "Done."}
"""What either vendor's start call answers, as far as each reads it: Skyvern's run finished at once."""


@pytest.mark.parametrize(("name", "part"), PINS, ids=[f"{n}:{p}" for n, p in PINS])
async def test_pinning_a_part_sends_exactly_its_wire_beside_the_task_and_the_callers_options(
    name: str, part: str
) -> None:
    request = BrowseRequest(task=TASK, start_url=START, provider_options=CALLER)
    plain = await sent("browse", name, request, reply=STARTED)
    pinned = await sent("browse", f"{name}:{part}", request, reply=STARTED)

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
        create_browse_provider(pin, api_key="k", transport=recording(bodies, STARTED)).submit(
            BrowseRequest(task=TASK, **options)
        )
    assert bodies == []


def test_a_pin_that_names_another_method_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="pinned browse backend"):
        get_browse_provider_class("answer:skyvern:skyvern-2.0")


def test_a_named_unpriced_pin_never_sends_under_a_cost_cap() -> None:
    bodies: list[dict[str, Any]] = []
    router = WebRouter(provider_config={"skyvern": {"api_key": "k", "transport": recording(bodies, STARTED)}})
    with pytest.raises(BudgetExceededError):
        router.browse(TASK, provider="skyvern:skyvern-2.0", budget=Budget(max_cost_usd=1.0))
    assert bodies == []
