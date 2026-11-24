"""A vendor mode the member refuses for this request: dropped from the candidates with why, never raised on the call.

Offline: Parallel's and Tavily's searches are replaced after validation; nothing is sent, and no key is real.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.candidates_helpers import Shown, clear_keys, ids, set_keys
from web_router import RouteDecision, WebRouter
from web_router.providers.parallel_search import ParallelProvider
from web_router.providers.tavily import TavilyProvider
from web_router.routing.candidates import build_candidates
from web_router.types import SearchRequest, SearchResponse, SearchResult

CHAIN = {"auto": {"chain": {"search": ["parallel", "tavily"]}}}
EXTRA = {"parallel": ["low"]}
LOW = "search:parallel:low"
PATH, BARE = ["example.com/docs"], ["example.com"]


@pytest.fixture(name="served")
def served_fixture(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    """Parallel and Tavily keyed and faked past validation; each call's backend and mode, in order."""
    set_keys(clear_keys(monkeypatch), "parallel", "tavily")
    served: list[tuple[str, str | None]] = []

    def fake(name: str) -> Any:
        def search(self: Any, request: SearchRequest) -> SearchResponse:
            served.append((name, request.mode))
            return SearchResponse(provider=name, query=request.query, results=[SearchResult(url="https://a.example/")])

        return search

    monkeypatch.setattr(ParallelProvider, "_search", fake("parallel"))
    monkeypatch.setattr(TavilyProvider, "_search", fake("tavily"))
    return served


def test_a_mode_refused_for_the_request_is_dropped_with_why_and_kept_when_it_is_not(served: Any) -> None:
    router = WebRouter(provider_config=CHAIN)
    refused = build_candidates(router, {"search": SearchRequest(query="q", include_domains=PATH)}, extra_modes=EXTRA)
    assert ids(refused) == ["search:parallel", "search:tavily"]
    assert "refuses this mode for this request" in refused.dropped[LOW]
    kept = build_candidates(router, {"search": SearchRequest(query="q", include_domains=BARE)}, extra_modes=EXTRA)
    assert ids(kept) == ["search:parallel", LOW, "search:tavily"]


@pytest.mark.parametrize(("domains", "backend"), [(PATH, ("tavily", None)), (BARE, ("parallel", "low"))])
def test_a_routed_call_passes_over_a_refused_mode_to_the_next_attempt(
    domains: list[str], backend: tuple[str, str | None], served: Any
) -> None:
    router = Shown(RouteDecision(attempts=[LOW, "search:tavily"]), extra_modes=EXTRA)
    with WebRouter(provider_config=CHAIN, router=router) as wr:
        wr.search("q", include_domains=domains)
    assert served == [backend]
