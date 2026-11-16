"""The keyless tier under a cost cap, on the shipped chains and price list: what it is priced at, and its order.

Offline: every key is taken out of the environment, so each method's ``auto``
is its keyless tier; members are built as ``auto`` builds them and nothing sends.
"""

from __future__ import annotations

import pytest

from tests.candidates_helpers import clear_keys, one
from web_router import Budget, WebRouter, prices
from web_router.constants import PROVIDER_AUTO
from web_router.constants_candidates import AUTO_GETTERS
from web_router.routing import cost_order
from web_router.routing.candidates import build_candidates
from web_router.routing.contract import ToolName
from web_router.types import ExtractRequest, NormalizedRequest, SearchRequest

CAP = Budget(max_cost_usd=1.0)
REQUESTS: dict[ToolName, NormalizedRequest] = {
    "search": SearchRequest(query="q"),
    "extract": ExtractRequest(urls=["https://a.test"]),
}


@pytest.mark.parametrize("tool", ["search", "extract"])
def test_keyless_nimble_is_priced_as_keyless_tavily_is_at_its_keyed_list_price(
    monkeypatch: pytest.MonkeyPatch, tool: ToolName
) -> None:
    clear_keys(monkeypatch)
    found = build_candidates(WebRouter(router=None), {tool: REQUESTS[tool]}, budget=CAP)
    for provider in ("nimble", "tavily"):
        assert one(found, f"{tool}:{provider}").est_cost_usd == prices.estimate(provider, tool)


@pytest.mark.parametrize("tool", ["search", "extract"])
def test_with_no_key_a_cost_cap_keeps_the_keyless_tier_in_its_order(
    monkeypatch: pytest.MonkeyPatch, tool: ToolName
) -> None:
    """No benchmark ranks a keyless backend, flagged ``allow_keyless`` or keyless by nature (Jina, the MCP ones)."""
    clear_keys(monkeypatch)
    auto = getattr(WebRouter(router=None), AUTO_GETTERS[tool])(PROVIDER_AUTO)
    assert cost_order.cost_route(auto, tool, REQUESTS[tool], CAP) == (None, None)
