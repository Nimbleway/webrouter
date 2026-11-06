"""The candidate builder: every (tool, provider) a router can serve a call with, priced, timed and prefiltered.

Offline: the members are read from their classes and the shipped price list;
nothing is built that sends, and no key is real.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from web_router import Budget, WebRouter
from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    BROWSE_ENV_BY_PROVIDER,
    ENV_BY_PROVIDER,
    EXTRACT_ENV_BY_PROVIDER,
    RESEARCH_ENV_BY_PROVIDER,
)
from web_router.exceptions import ProviderNotInstalledError
from web_router.prices import estimate, estimate_latency
from web_router.registry import get_extract_provider_class, get_provider_class, get_research_provider_class
from web_router.routing.candidates import CandidateSet, build_candidates, default_requests
from web_router.types import ExtractRequest, ResearchRequest, SearchRequest

_TABLES = (ANSWER_ENV_BY_PROVIDER, BROWSE_ENV_BY_PROVIDER, EXTRACT_ENV_BY_PROVIDER, RESEARCH_ENV_BY_PROVIDER)
_ALL_ENV = {var for table in _TABLES for names in table.values() for var in names} | {
    var for var in ENV_BY_PROVIDER.values() if var
}


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """No key in the environment; a test sets the ones it needs."""
    for var in _ALL_ENV:
        monkeypatch.delenv(var, raising=False)
    yield monkeypatch


def _set_keys(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    for name in names:
        for table in _TABLES:
            for var in table.get(name, ()):
                monkeypatch.setenv(var, "test-key")
        if search_var := ENV_BY_PROVIDER.get(name):
            monkeypatch.setenv(search_var, "test-key")


def _ids(found: CandidateSet, tool: str | None = None) -> list[str]:
    return [c.id for c in found.candidates if tool is None or c.tool == tool]


def _one(found: CandidateSet, candidate_id: str):  # type: ignore[no-untyped-def]
    return next(c for c in found.candidates if c.id == candidate_id)


def test_ids_are_tool_colon_provider(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "linkup")
    found = build_candidates(WebRouter(), default_requests("q", ["https://a.test"]))
    assert found.candidates
    assert all(c.id == f"{c.tool}:{c.provider}" for c in found.candidates)


def test_keyless_providers_are_search_and_extract_candidates_with_no_key(keys: pytest.MonkeyPatch) -> None:
    found = build_candidates(WebRouter(), default_requests("q", ["https://a.test"]))
    assert _ids(found, "search") == [
        "search:tavily", "search:you-mcp", "search:exa-mcp", "search:parallel-mcp", "search:keenable",
    ]  # fmt: skip
    assert _ids(found, "extract") == ["extract:tavily", "extract:jina", "extract:firecrawl", "extract:keenable"]
    # Answer, research and browse have no keyless tier.
    assert {c.tool for c in found.candidates} == {"search", "extract"}
    # The keyless tier is hand-ordered: none of it is backed by route-eval, keyed Tavily's rank included.
    assert set(found.unmeasured) == set(_ids(found))


def test_the_configured_chain_is_the_candidate_set_in_its_order(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "brave", "exa", "perplexity")
    router = WebRouter(provider_config={"auto": {"chain": {"search": ["exa", "brave", "exa"]}}})
    found = build_candidates(router, {"search": SearchRequest(query="q")})
    # Perplexity holds a key but is not in the chain; exa, named twice, is one candidate at its first place.
    assert _ids(found) == ["search:exa", "search:brave"]


def test_the_default_chain_holds_every_keyed_backend(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "brave", "exa")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q")})
    keyed = [i for i in _ids(found) if i in ("search:exa", "search:brave")]
    assert sorted(keyed) == ["search:brave", "search:exa"]
    assert _ids(found)[:2] == keyed  # keyed members come before the keyless tier


def test_a_pinned_tool_offers_only_its_own_backends(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "linkup")
    found = build_candidates(WebRouter(), {"research": ResearchRequest(query="q")})
    assert _ids(found) == ["research:linkup"]


@pytest.mark.parametrize("pinned", ["exa", "EXA"])
def test_a_pinned_provider_leaves_nothing_to_route(keys: pytest.MonkeyPatch, pinned: str) -> None:
    _set_keys(keys, "exa")
    assert build_candidates(WebRouter(), default_requests("q"), provider=pinned) == CandidateSet()
    assert build_candidates(WebRouter(), default_requests("q"), provider="auto").candidates


def test_extract_needs_urls(keys: pytest.MonkeyPatch) -> None:
    assert not _ids(build_candidates(WebRouter(), default_requests("q")), "extract")
    assert "extract" not in default_requests("q")
    assert _ids(build_candidates(WebRouter(), default_requests("q", ["https://a.test"])), "extract")


def test_an_unsupported_option_leaves_out_the_members_that_would_drop_it(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "brave", "exa", "perplexity", "linkup")
    router = WebRouter()
    every = _ids(build_candidates(router, {"search": SearchRequest(query="q")}))
    found = build_candidates(router, {"search": SearchRequest(query="q", include_domains=["a.test"])})
    assert found.candidates
    assert len(found.candidates) < len(every)
    assert all("include_domains" in get_provider_class(c.provider).supported_parameters for c in found.candidates)


def test_an_unsupported_format_leaves_out_the_extract_members_that_would_relabel_it(keys: pytest.MonkeyPatch) -> None:
    found = build_candidates(WebRouter(), {"extract": ExtractRequest(urls=["https://a.test"], format="html")})
    every = build_candidates(WebRouter(), {"extract": ExtractRequest(urls=["https://a.test"])})
    assert len(found.candidates) < len(every.candidates)
    assert all("html" in get_extract_provider_class(c.provider).supported_formats for c in found.candidates)


def test_an_option_no_member_maps_leaves_the_method_with_no_candidates(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "perplexity")
    found = build_candidates(WebRouter(), {"research": ResearchRequest(query="q", output_schema={"type": "object"})})
    assert "output_schema" not in get_research_provider_class("perplexity").supported_parameters
    assert found == CandidateSet()


def test_a_member_whose_extra_is_missing_is_no_candidate(keys: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> type:
        if name == "you-mcp":
            raise ProviderNotInstalledError(name, "mcp")
        return get_provider_class(name)

    keys.setattr("web_router.registry.get_provider_class", missing)
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q")})
    assert "search:tavily" in _ids(found)
    assert "search:you-mcp" not in _ids(found)


def test_an_answer_candidate_carries_its_price_and_p90(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "linkup")
    found = build_candidates(WebRouter(), default_requests("q"))
    answer = _one(found, "answer:linkup")
    assert answer.est_cost_usd == estimate("linkup", "answer")
    assert answer.est_latency_s == estimate_latency("linkup", "answer")[1]  # type: ignore[index]
    assert "answer:linkup" not in found.unmeasured  # ranked by route-eval, priced and timed


def test_extract_cost_is_per_page(keys: pytest.MonkeyPatch) -> None:
    urls = ["https://a.test", "https://b.test", "https://c.test"]
    found = build_candidates(WebRouter(), {"extract": ExtractRequest(urls=urls)})
    assert _one(found, "extract:tavily").est_cost_usd == pytest.approx(3 * estimate("tavily", "extract"))  # type: ignore[operator]


def test_search_is_priced_at_the_depth_the_caller_set(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "nimble")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q", search_depth="deep")})
    assert _one(found, "search:nimble").est_cost_usd == estimate("nimble", "search", "deep")


def test_no_budget_drops_nothing(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "webiq", "exa")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q")})
    assert _one(found, "search:webiq").est_cost_usd is None  # unknown price, kept: no cap
    assert found.dropped == {}


def test_a_cost_cap_drops_known_estimates_over_what_is_left(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "brave", "exa")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q")}, budget=Budget(max_cost_usd=0.01))
    assert "search:brave" in _ids(found)  # $0.005
    assert "search:exa" not in _ids(found)  # $0.017
    assert "over" in found.dropped["search:exa"]


def test_a_cost_cap_drops_unknown_prices(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "webiq", "brave")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q")}, budget=Budget(max_cost_usd=10.0))
    assert "search:webiq" not in _ids(found)
    assert "no known price" in found.dropped["search:webiq"]
    assert "search:brave" in _ids(found)


def test_research_without_effort_stays_when_a_cheaper_effort_fits(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "linkup")
    request = {"research": ResearchRequest(query="q")}
    assert estimate("linkup", "research") == 1.5  # its default, high
    found = build_candidates(WebRouter(), request, budget=Budget(max_cost_usd=1.0))
    assert _one(found, "research:linkup").est_cost_usd == estimate("linkup", "research", "low") == 0.25
    # With room for its default, it is priced at its default.
    roomy = build_candidates(WebRouter(), request, budget=Budget(max_cost_usd=2.0))
    assert _one(roomy, "research:linkup").est_cost_usd == 1.5
    # Dropped only when no effort fits.
    tight = build_candidates(WebRouter(), request, budget=Budget(max_cost_usd=0.1))
    assert _ids(tight) == []
    assert "research:linkup" in tight.dropped


def test_research_with_a_pinned_effort_is_priced_at_that_effort(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "linkup")
    request = {"research": ResearchRequest(query="q", effort="high")}
    found = build_candidates(WebRouter(), request, budget=Budget(max_cost_usd=1.0))
    assert _ids(found) == []
    assert found.dropped["research:linkup"].startswith("estimated $1.5")


def test_a_time_cap_drops_a_known_p90_that_does_not_fit(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "brave", "exa")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q")}, budget=Budget(time_limit_s=2.0))
    assert "search:brave" in _ids(found)  # p90 under a second
    assert "search:exa" not in _ids(found)  # p90 over six seconds
    assert "p90" in found.dropped["search:exa"]


def test_an_unknown_p90_research_candidate_stays_under_a_time_cap(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "linkup")
    found = build_candidates(WebRouter(), {"research": ResearchRequest(query="q")}, budget=Budget(time_limit_s=1.0))
    candidate = _one(found, "research:linkup")
    assert candidate.est_latency_s is None
    assert "research:linkup" in found.unmeasured


def test_strict_drops_unknown_p90_under_a_time_cap(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "linkup", "brave")
    requests = {"search": SearchRequest(query="q"), "research": ResearchRequest(query="q")}
    found = build_candidates(WebRouter(), requests, budget=Budget(time_limit_s=60.0, strict=True))
    assert "research:linkup" not in _ids(found)
    assert "strict" in found.dropped["research:linkup"]
    assert "search:brave" in _ids(found)  # measured, and it fits
    # Strict speaks only to time: with no time cap it drops nothing.
    assert "research:linkup" in _ids(build_candidates(WebRouter(), requests, budget=Budget(strict=True)))


def test_hand_ordered_methods_are_unmeasured(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, "linkup", "brave")
    found = build_candidates(WebRouter(), default_requests("q", ["https://a.test"]))
    for candidate in found.candidates:
        if candidate.tool in ("extract", "research", "browse"):
            assert candidate.id in found.unmeasured
    assert "search:brave" not in found.unmeasured


def test_it_is_fast_enough_for_every_routed_call(keys: pytest.MonkeyPatch) -> None:
    _set_keys(keys, *RESEARCH_ENV_BY_PROVIDER, *ANSWER_ENV_BY_PROVIDER, *ENV_BY_PROVIDER)
    router = WebRouter()
    requests = default_requests("q", ["https://a.test"])
    budget = Budget(max_cost_usd=1.0, time_limit_s=10.0)
    build_candidates(router, requests, budget=budget)  # builds and caches each auto, once per router
    runs = 200
    started = time.perf_counter()
    for _ in range(runs):
        build_candidates(router, requests, budget=budget)
    assert (time.perf_counter() - started) / runs < 0.001
