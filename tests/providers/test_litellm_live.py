"""Exa, Tavily and Parallel search through a live LiteLLM proxy.

Run with ``LITELLM_PROXY_API_KEY`` and ``LITELLM_PROXY_API_BASE`` set to a proxy listing ``exa``, ``tavily`` and
``parallel`` search tools; without them every test here skips. The offline tests are in ``test_litellm.py``.
"""

from __future__ import annotations

import os

import pytest

LIVE = pytest.mark.skipif(
    not (os.getenv("LITELLM_PROXY_API_KEY") and os.getenv("LITELLM_PROXY_API_BASE")),
    reason="LITELLM_PROXY_API_KEY and LITELLM_PROXY_API_BASE not set",
)


@LIVE
@pytest.mark.integration
@pytest.mark.parametrize("name", ["exa-litellm", "tavily-litellm", "parallel-litellm"])
def test_live_search_returns_results(name: str) -> None:
    from web_router import WebRouter

    with WebRouter(name) as router:
        response = router.search("Who directed the 2005 film Pride and Prejudice?", max_results=3)

    assert response.provider == name
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") and result.content for result in response.results)


@LIVE
@pytest.mark.integration
@pytest.mark.parametrize("name", ["exa-litellm", "tavily-litellm", "parallel-litellm"])
def test_live_a_domain_filter_is_honoured(name: str) -> None:
    from web_router import WebRouter

    with WebRouter(name) as router:
        response = router.search("retrieval augmented generation", max_results=3, include_domains=["wikipedia.org"])

    assert response.results and all("wikipedia.org" in result.url for result in response.results)


@LIVE
@pytest.mark.integration
@pytest.mark.parametrize("name", ["exa-litellm", "parallel-litellm"])
@pytest.mark.parametrize("effort", ["low", "high"])
def test_live_every_effort_is_accepted(name: str, effort: str) -> None:
    from web_router import WebRouter

    with WebRouter(name) as router:
        response = router.search("what is retrieval augmented generation", max_results=2, effort=effort)

    assert response.results


@LIVE
@pytest.mark.integration
@pytest.mark.parametrize("name", ["exa-litellm", "tavily-litellm", "parallel-litellm"])
def test_live_lite_comes_back_short(name: str) -> None:
    from web_router import WebRouter

    with WebRouter(name) as router:
        response = router.search("what is retrieval augmented generation", max_results=2, search_depth="lite")

    assert response.results and max(len(result.content) for result in response.results) <= 600
