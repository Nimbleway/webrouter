"""Perplexity research citations: the pages its searches found, then the pages its agent opened."""

from __future__ import annotations

import pytest

from tests.providers._perplexity_research_stub import COMPLETED, FINISHED_OUTPUT, QUESTION, run, stub
from web_router.providers.perplexity import PerplexityResearchProvider
from web_router.types import ResearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> PerplexityResearchProvider:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    return PerplexityResearchProvider()


def test_search_results_become_citations(provider: PerplexityResearchProvider) -> None:
    stub(provider, started=COMPLETED)
    [citation] = provider.research(ResearchRequest(query=QUESTION)).citations

    assert citation.url == "https://en.wikipedia.org/wiki/Eiffel_Tower"
    assert (citation.title, citation.snippet) == ("Eiffel Tower", "Opened on 31 March 1889")
    assert citation.extra["date"] == "2026-01-01"


def test_pages_the_agent_opened_are_cited_beside_the_ones_it_found(provider: PerplexityResearchProvider) -> None:
    """``fetch_url_results`` lists the pages the agent read, under ``contents``; one it also found is cited once."""
    opened = {
        "type": "fetch_url_results",
        "contents": [
            {"url": "https://www.toureiffel.paris/en/history", "title": "History", "snippet": "Inaugurated in 1889"},
            {"url": "https://en.wikipedia.org/wiki/Eiffel_Tower", "title": "Eiffel Tower", "snippet": "Full page"},
        ],
    }
    stub(provider, started=run("completed", output=[*FINISHED_OUTPUT, opened], incomplete_details=None))
    citations = provider.research(ResearchRequest(query=QUESTION)).citations

    assert [citation.url for citation in citations] == [
        "https://en.wikipedia.org/wiki/Eiffel_Tower",
        "https://www.toureiffel.paris/en/history",
    ]
    assert citations[1].snippet == "Inaugurated in 1889"
