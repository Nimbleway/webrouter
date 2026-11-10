"""The Nimble research provider's citations for a run without trust, over a stubbed Web Search Agent client."""

from __future__ import annotations

from typing import Any

import pytest

from web_router.types import ResearchRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from tests.providers._nimble_research_stub import research_run, stub_client
from web_router.providers.nimble import NimbleResearchProvider

SCHEMA = {"type": "object", "properties": {"founded": {"type": "string"}}}

# Recorded from a live low-effort run without trust (task_run_475908a24f724a8d8cb7b97fe3d6a627).
NO_TRUST_RESULT = {
    "run": research_run("completed", trust_mode="no_trust"),
    "output": {
        "type": "json",
        "content": {"products": ["Web Search Agents (Agent API)", "Search API", "Extract API"]},
        "trust": None,
        "sources": [
            {
                "url": "https://llms.nimbleway.com/nimble-platform-products-and-apis",
                "title": "Nimble Platform, Products, and APIs",
            },
            {"url": "https://nimbleway.com/pricing", "title": "Nimble Pricing | Web Search Agent Platform & SDK Plans"},
        ],
    },
}

TRUST: dict[str, Any] = {
    "confidence": "high",
    "reasoning": "primary source",
    "claims": [
        {
            "path": "$.founded",
            "confidence": "high",
            "reasoning": "stated on the company site",
            "citations": [{"url": "https://a.example/about", "title": "About", "excerpts": ["Founded in 2019"]}],
        }
    ],
    "sources": [{"url": "https://a.example/about", "title": "About", "type": "primary"}],
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleResearchProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleResearchProvider()


def test_a_run_without_trust_is_cited_from_its_output_sources(provider: NimbleResearchProvider) -> None:
    stub_client(provider, started=research_run("completed"), result=NO_TRUST_RESULT)

    citations = provider.research(ResearchRequest(query="q", output_schema=SCHEMA)).citations

    assert [(citation.url, citation.title, citation.field) for citation in citations] == [
        (
            "https://llms.nimbleway.com/nimble-platform-products-and-apis",
            "Nimble Platform, Products, and APIs",
            None,
        ),
        ("https://nimbleway.com/pricing", "Nimble Pricing | Web Search Agent Platform & SDK Plans", None),
    ]


def test_output_sources_follow_the_trust_citations_once_each(provider: NimbleResearchProvider) -> None:
    output = {
        "type": "json",
        "content": {"founded": "2019"},
        "trust": TRUST,
        "sources": [
            {"url": "https://a.example/about", "title": "About"},
            {"url": "https://c.example/blog", "title": "Blog"},
        ],
    }
    stub_client(
        provider, started=research_run("completed"), result={"run": research_run("completed"), "output": output}
    )

    citations = provider.research(ResearchRequest(query="q", output_schema=SCHEMA)).citations

    assert [(citation.url, citation.field) for citation in citations] == [
        ("https://a.example/about", "$.founded"),
        ("https://c.example/blog", None),
    ]
