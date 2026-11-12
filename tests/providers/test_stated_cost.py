"""A backend's own stated cost: read into ``cost_usd`` as ``reported``, and nothing read when it states none.

Perplexity's Agent API states ``usage.cost.total_cost`` beside ``currency: "USD"``
(seen live on 2026-11-12: ``0.00105`` for one answer), and Exa ``costDollars.total``
on search and answer (``0.007`` a search, ``0.005`` an answer).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.providers._answer_stub import stub_answer_api
from tests.providers._perplexity_answer_payload import answered
from web_router.providers._cost import stated_cost
from web_router.providers.exa import ExaAnswerProvider, ExaProvider
from web_router.providers.perplexity import PerplexityAnswerProvider, PerplexityResearchProvider
from web_router.types import ResearchRequest, SearchRequest
from web_router.types_answer import AnswerRequest

PERPLEXITY_USAGE = {"usage": {"cost": {"currency": "USD", "tool_calls_cost": 0.001, "total_cost": 0.00105}}}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"cost": {"total": 0.007}}, {"cost_usd": 0.007, "cost_source": "reported"}),
        ({"cost": {"total": 0}}, {"cost_usd": 0.0, "cost_source": "reported"}),
        ({"cost": {"total": 1, "currency": "usd"}}, {"cost_usd": 1.0, "cost_source": "reported"}),
        ({"cost": {"total": 1, "currency": "EUR"}}, {}),
        ({"cost": {"total": "0.007"}}, {}),
        ({"cost": {"total": True}}, {}),
        ({"cost": {"total": -1}}, {}),
        ({"cost": {"total": float("nan")}}, {}),
        ({"cost": None}, {}),
        ({}, {}),
        ([], {}),
    ],
)
def test_a_stated_cost_is_read_only_when_it_is_a_dollar_amount(payload: Any, expected: dict[str, Any]) -> None:
    assert stated_cost(payload, "cost", "total") == expected


def test_a_perplexity_answer_reports_its_total_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    provider = PerplexityAnswerProvider()
    stub_answer_api(provider, {**answered(), **PERPLEXITY_USAGE})
    response = provider.answer(AnswerRequest(query="q"))
    assert (response.cost_usd, response.cost_source) == (0.00105, "reported")


def test_a_perplexity_answer_with_no_usage_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    provider = PerplexityAnswerProvider()
    stub_answer_api(provider, {key: value for key, value in answered().items() if key != "usage"})
    response = provider.answer(AnswerRequest(query="q"))
    assert (response.cost_usd, response.cost_source) == (None, None)


def test_a_perplexity_research_run_reports_its_total_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    response = PerplexityResearchProvider()._to_response(ResearchRequest(query="q"), {**answered(), **PERPLEXITY_USAGE})
    assert (response.cost_usd, response.cost_source) == (0.00105, "reported")


def test_an_exa_answer_reports_its_cost_dollars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    provider = ExaAnswerProvider()
    stub_answer_api(provider, {"answer": "a", "citations": [], "costDollars": {"total": 0.005}})
    response = provider.answer(AnswerRequest(query="q"))
    assert (response.cost_usd, response.cost_source) == (0.005, "reported")


def test_an_exa_search_reports_its_cost_dollars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    payload = {"results": [], "costDollars": {"total": 0.007, "search": {"neural": 0.007}}}
    response = ExaProvider()._to_response(SearchRequest(query="q"), payload)
    assert (response.cost_usd, response.cost_source) == (0.007, "reported")
