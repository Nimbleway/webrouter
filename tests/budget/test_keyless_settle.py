"""A keyless call reports $0 as ``cost_source="keyless"``, and a budget settles it to that.

Offline: each provider is built with every key taken out of the environment, and its response is read from a
payload the test hands it; nothing is sent.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.budget.conftest import Chain, Table, scoped
from tests.candidates_helpers import clear_keys
from web_router import Budget, WebRouter
from web_router.constants_cost import COST_ESTIMATED, COST_KEYLESS, COST_REPORTED
from web_router.providers.firecrawl import FirecrawlExtractProvider
from web_router.providers.keenable import KeenableProvider
from web_router.providers.keenable_extract import KeenableExtractProvider
from web_router.providers.nimble import NimbleExtractProvider, NimbleProvider
from web_router.providers.tavily import TavilyProvider
from web_router.providers.tavily_extract import TavilyExtractProvider
from web_router.routing.metered import collected_cost
from web_router.types import ExtractRequest, SearchRequest, SearchResponse

SEARCH, EXTRACT = SearchRequest(query="q"), ExtractRequest(urls=["https://a.test"])
EMPTY: dict[str, Any] = {"results": []}

READS = [
    (NimbleProvider, (SEARCH, EMPTY)),
    (TavilyProvider, (SEARCH, EMPTY)),
    (KeenableProvider, (SEARCH, EMPTY)),
    (NimbleExtractProvider, (EXTRACT, ["https://a.test"], {"status": "failed"})),
    (TavilyExtractProvider, (EXTRACT, ["https://a.test"], EMPTY)),
    (FirecrawlExtractProvider, (EXTRACT, ["https://a.test"], {"success": False})),
    (KeenableExtractProvider, (EXTRACT, ["https://a.test"], {"content": "page"})),
]
"""Each keyless-capable provider, and what its ``_to_response`` is read with."""


@pytest.mark.parametrize(("cls", "args"), READS, ids=[cls.__name__ for cls, _ in READS])
def test_a_keyless_call_reports_zero_and_a_keyed_one_leaves_the_cost_to_the_router(
    monkeypatch: pytest.MonkeyPatch, cls: type, args: tuple[Any, ...]
) -> None:
    clear_keys(monkeypatch)
    keyless, keyed = cls(allow_keyless=True), cls(api_key="test-key")
    try:
        assert (keyless._to_response(*args).cost_usd, keyless._to_response(*args).cost_source) == (0.0, COST_KEYLESS)
        assert keyed._to_response(*args).cost_source is None
    finally:
        keyless.close()
        keyed.close()


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_budget_settles_a_keyless_answer_to_zero(
    chain: Chain, priced: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = chain.tool.fake._respond

    def keyless(self: object, *args: object) -> object:
        return real(self, *args).model_copy(update={"cost_usd": 0.0, "cost_source": COST_KEYLESS})

    monkeypatch.setattr(chain.tool.fake, "_respond", keyless)
    priced.update(first=(0.008, None))  # the keyed list price, checked before the call
    with WebRouter().budget(Budget(max_cost_usd=0.10)) as shared:
        response = scoped(chain, shared)
    assert (response.cost_usd, response.cost_source) == (0.0, COST_KEYLESS)
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.0, 0.0)


@pytest.mark.parametrize(
    ("sources", "summed"),
    [
        ((COST_KEYLESS, COST_KEYLESS), COST_KEYLESS),
        ((COST_KEYLESS, COST_REPORTED), COST_REPORTED),
        ((COST_KEYLESS, COST_ESTIMATED), COST_ESTIMATED),
    ],
)
def test_a_sum_of_parts_keeps_their_source_when_they_share_one(sources: tuple[str, str], summed: str) -> None:
    parts = [SearchResponse(provider="p", query="q", cost_usd=0.0, cost_source=source) for source in sources]
    assert collected_cost(parts) == {"cost_usd": 0.0, "cost_source": summed}
