"""Nimble research citations when the run's bibliography comes in ``output.sources``, not ``trust``.

Nimble's agents API sends ``output.trust`` as null (or with no sources) and lists what the run
read in ``output.sources``; the trust-shaped payloads are in ``test_nimble_research.py``.
"""

from __future__ import annotations

import pytest

from web_router.types import ResearchRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from tests.providers.test_nimble_research import JSON_RESULT, SCHEMA, _run, _stub
from web_router.providers.nimble import NimbleResearchProvider


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleResearchProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleResearchProvider()


UNTRUSTED_RESULT = {
    "run": _run("completed"),
    "output": {
        "type": "json",
        "content": {"founded": "2019"},
        "trust": None,
        "sources": [
            {"url": "https://a.example/about", "title": "About", "type": "primary"},
            {"url": "https://b.example/news", "title": None},
            # The same page listed twice is one citation.
            {"url": "https://a.example/about", "title": "About"},
        ],
    },
}


def test_a_run_with_no_trust_payload_is_cited_from_its_output_sources(provider: NimbleResearchProvider) -> None:
    _stub(provider, started=_run("completed"), result=UNTRUSTED_RESULT)

    response = provider.research(ResearchRequest(query="q", output_schema=SCHEMA))

    assert response.structured == {"founded": "2019"}
    assert [(citation.url, citation.title, citation.field) for citation in response.citations] == [
        ("https://a.example/about", "About", None),
        ("https://b.example/news", "", None),
    ], "each source once, unattributed, in the order Nimble listed them"
    assert response.citations[0].extra == {"type": "primary"}


async def test_the_async_path_reads_output_sources_too(provider: NimbleResearchProvider) -> None:
    _stub(provider, started=_run("completed"), result=UNTRUSTED_RESULT)

    response = await provider.aresearch(ResearchRequest(query="q", output_schema=SCHEMA))

    assert response.urls == ["https://a.example/about", "https://b.example/news"]


def test_a_trust_payload_that_lists_no_sources_falls_back_to_output_sources(provider: NimbleResearchProvider) -> None:
    result = {**UNTRUSTED_RESULT, "output": {**UNTRUSTED_RESULT["output"], "trust": {"claims": [], "sources": []}}}
    _stub(provider, started=_run("completed"), result=result)

    assert provider.research(ResearchRequest(query="q", output_schema=SCHEMA)).urls == [
        "https://a.example/about",
        "https://b.example/news",
    ]


def test_output_sources_never_add_to_a_trust_payload_that_lists_its_own(provider: NimbleResearchProvider) -> None:
    result = {**JSON_RESULT, "output": {**JSON_RESULT["output"], "sources": [{"url": "https://c.example/other"}]}}
    _stub(provider, started=_run("completed"), result=result)

    assert provider.research(ResearchRequest(query="q", output_schema=SCHEMA)).urls == ["https://a.example/about"]
