"""The candidate builder's membership: which (tool, provider) pairs a call's `auto` chains offer a router.

Pricing and the budget prefilter are in ``test_candidates_budget.py``.
"""

from __future__ import annotations

import pytest

from tests.candidates_helpers import clear_keys, ids, set_keys
from web_router import WebRouter
from web_router.constants import EXTRACT_ENV_BY_PROVIDER
from web_router.constants_candidates import MSG_DROPPED_NOT_CONFIGURED, MSG_DROPPED_NOT_INSTALLED
from web_router.exceptions import ProviderNotInstalledError
from web_router.providers.auto import AutoResearchProvider
from web_router.registry import get_extract_provider_class, get_provider_class, get_research_provider_class
from web_router.routing.candidates import CandidateSet, build_candidates, default_requests
from web_router.types import ExtractRequest, ResearchRequest, SearchRequest


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No key in the environment; a test sets the ones it needs."""
    return clear_keys(monkeypatch)


def test_keyless_providers_are_search_and_extract_candidates_with_no_key(keys: pytest.MonkeyPatch) -> None:
    found = build_candidates(WebRouter(), default_requests("q", ["https://a.test"]))
    assert ids(found, "search") == [
        "search:tavily", "search:you-mcp", "search:exa-mcp", "search:parallel-mcp", "search:keenable",
    ]  # fmt: skip
    assert ids(found, "extract") == ["extract:tavily", "extract:jina", "extract:firecrawl", "extract:keenable"]
    # Answer, research and browse have no keyless tier.
    assert {c.tool for c in found.candidates} == {"search", "extract"}
    # The keyless tier is hand-ordered: none of it is backed by route-eval, keyed Tavily's rank included.
    assert set(found.unmeasured) == set(ids(found))


def test_the_configured_chain_is_the_candidate_set_in_its_order(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "brave", "exa", "perplexity")
    router = WebRouter(provider_config={"auto": {"chain": {"search": ["exa", "brave", "exa"]}}})
    found = build_candidates(router, {"search": SearchRequest(query="q")})
    # Perplexity holds a key but is not in the chain; exa, named twice, is one candidate at its first place.
    assert ids(found) == ["search:exa", "search:brave"]


def test_the_default_chain_holds_every_keyed_backend(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "brave", "exa")
    found = build_candidates(WebRouter(), {"search": SearchRequest(query="q")})
    keyed = [i for i in ids(found) if i in ("search:exa", "search:brave")]
    assert sorted(keyed) == ["search:brave", "search:exa"]
    assert ids(found)[:2] == keyed  # keyed members come before the keyless tier


def test_a_pinned_tool_offers_only_its_own_backends(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "linkup")
    found = build_candidates(WebRouter(), {"research": ResearchRequest(query="q")})
    assert ids(found) == ["research:linkup"]


@pytest.mark.parametrize("pinned", ["exa", "EXA"])
def test_a_pinned_provider_leaves_nothing_to_route(keys: pytest.MonkeyPatch, pinned: str) -> None:
    set_keys(keys, "exa")
    assert build_candidates(WebRouter(), default_requests("q"), provider=pinned) == CandidateSet()
    assert build_candidates(WebRouter(), default_requests("q"), provider="auto").candidates


def test_extract_needs_urls(keys: pytest.MonkeyPatch) -> None:
    assert not ids(build_candidates(WebRouter(), default_requests("q")), "extract")
    assert "extract" not in default_requests("q")
    assert ids(build_candidates(WebRouter(), default_requests("q", ["https://a.test"])), "extract")


def test_an_unsupported_option_leaves_out_the_members_that_would_drop_it(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "brave", "exa", "perplexity", "linkup")
    router = WebRouter()
    every = ids(build_candidates(router, {"search": SearchRequest(query="q")}))
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
    set_keys(keys, "perplexity")
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
    assert "search:tavily" in ids(found)
    assert "search:you-mcp" not in ids(found)
    assert found.dropped["search:you-mcp"] == MSG_DROPPED_NOT_INSTALLED


def test_a_chain_member_with_no_credential_is_no_candidate(keys: pytest.MonkeyPatch) -> None:
    bare = WebRouter(provider_config={"auto": {"chain": {"search": ["tavily", "you-mcp"]}}})
    found = build_candidates(bare, {"search": SearchRequest(query="q")})
    assert ids(found) == ["search:you-mcp"]
    assert found.dropped == {"search:tavily": MSG_DROPPED_NOT_CONFIGURED}
    keyless = WebRouter(provider_config={"auto": {"chain": {"search": [("tavily", {"allow_keyless": True})]}}})
    assert ids(build_candidates(keyless, {"search": SearchRequest(query="q")})) == ["search:tavily"]
    keyed = WebRouter(provider_config={"auto": {"chain": {"search": ["tavily"]}}, "tavily": {"api_key": "test-key"}})
    assert ids(build_candidates(keyed, {"search": SearchRequest(query="q")})) == ["search:tavily"]


def test_an_error_other_than_no_member_left_raises(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "linkup")

    def broken(self: AutoResearchProvider, request: ResearchRequest) -> list[tuple[int, str]]:
        raise ProviderNotInstalledError("linkup", "research")

    keys.setattr(AutoResearchProvider, "_eligible", broken)
    with pytest.raises(ProviderNotInstalledError):
        build_candidates(WebRouter(), {"research": ResearchRequest(query="q")})


def test_a_research_member_whose_extra_is_missing_leaves_out_only_that_member(keys: pytest.MonkeyPatch) -> None:
    set_keys(keys, "nimble", "linkup")

    def missing(name: str) -> type:
        if name == "nimble":
            raise ProviderNotInstalledError(name, "research")
        return get_research_provider_class(name)

    keys.setattr("web_router.registry.get_research_provider_class", missing)
    found = build_candidates(WebRouter(), {"research": ResearchRequest(query="q")})
    assert ids(found) == ["research:linkup"]
    assert found.dropped == {"research:nimble": MSG_DROPPED_NOT_INSTALLED}


def test_a_member_missing_a_setting_beside_its_key_is_dropped_as_not_configured(keys: pytest.MonkeyPatch) -> None:
    token, _zone = EXTRACT_ENV_BY_PROVIDER["brightdata"]
    keys.setenv(token, "test-key")  # a key, but no zone: not a missing credential
    router = WebRouter(provider_config={"auto": {"chain": {"extract": ["brightdata", "jina"]}}})
    found = build_candidates(router, {"extract": ExtractRequest(urls=["https://a.test"])})
    assert ids(found) == ["extract:jina"]
    assert found.dropped == {"extract:brightdata": MSG_DROPPED_NOT_CONFIGURED}
    assert "credential" not in MSG_DROPPED_NOT_CONFIGURED
