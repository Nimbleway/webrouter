"""An unpriced pinned tier in a chain: dropped from the candidates under a cost cap, offered without one.

One case per method; each chain pairs a priced pin with an unpriced one, except browse, where no tier is priced.
"""

from __future__ import annotations

from typing import Any

import pytest

from web_router import Budget, WebRouter
from web_router.routing.candidates import build_candidates
from web_router.types import BrowseRequest, ExtractRequest
from web_router.types_answer import AnswerRequest

CHAINS: dict[str, tuple[list[str], Any, tuple[str, ...]]] = {
    "extract": (["nimble:nimble-max", "nimble:nimble-vx8"], ExtractRequest(urls=["https://a.example/"]), ("NIMBLE",)),
    "answer": (["linkup:linkup-deep", "exa:exa-pro"], AnswerRequest(query="Who won?"), ("LINKUP", "EXA")),
    "browse": (
        ["skyvern:skyvern-2.0", "browser-use:browser-use-o3"],
        BrowseRequest(task="Find the top story"),
        ("SKYVERN", "BROWSER_USE"),
    ),
}
PRICED = {"extract": ["nimble:nimble-max"], "answer": ["linkup:linkup-deep"], "browse": []}


@pytest.mark.parametrize("method", list(CHAINS))
@pytest.mark.parametrize("cap", [1.0, None])
def test_an_unpriced_tier_is_offered_only_without_a_cost_cap(
    monkeypatch: pytest.MonkeyPatch, method: str, cap: float | None
) -> None:
    chain, request, keys = CHAINS[method]
    for key in keys:
        monkeypatch.setenv(f"{key}_API_KEY", "k")
    router = WebRouter(provider_config={"auto": {"chain": {method: chain}}})
    found = build_candidates(router, {method: request}, budget=None if cap is None else Budget(max_cost_usd=cap))
    assert [candidate.provider for candidate in found.candidates] == (chain if cap is None else PRICED[method])
