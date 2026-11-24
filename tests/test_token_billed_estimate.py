"""A row billed by tokens is estimated at the benchmark's measured mean, at its default mode only."""

from __future__ import annotations

import pytest

from web_router import prices
from web_router._chains import chains, measured_accuracy, measured_cost
from web_router.prices_modes import mode_price
from web_router.routing.metered import estimated_cost
from web_router.types import ResearchRequest
from web_router.types_answer import AnswerRequest


def test_perplexitys_answer_is_priced_at_the_answer_scans_measured_mean() -> None:
    assert prices.price("perplexity", "answer").unit == "token_billed"
    measured = chains()["answer"]["measured"]["perplexity"]["cost_usd"]
    assert measured is not None
    assert mode_price("perplexity", "answer", None) == pytest.approx(measured)
    assert estimated_cost("answer", "perplexity", AnswerRequest(query="q")) == pytest.approx(measured)


def test_a_token_billed_row_no_benchmark_measured_stays_unknown() -> None:
    assert measured_cost("research", "perplexity") is None
    assert mode_price("perplexity", "research", "high") is None
    assert estimated_cost("research", "perplexity", ResearchRequest(query="q")) is None


def test_a_listed_price_is_never_replaced_by_a_measured_mean() -> None:
    assert mode_price("exa", "answer", None) == prices.estimate("exa", "answer")


@pytest.mark.parametrize("missing", [("nothing", "perplexity"), ("answer", "nobody")])
def test_a_method_or_backend_the_benchmark_never_ran_has_no_measured_mean(missing: tuple[str, str]) -> None:
    assert measured_cost(*missing) is None


def test_measured_accuracy_keeps_only_rows_with_a_share(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = {"a": {"accuracy": 0.5}, "b": {"accuracy": None}, "c": {"accuracy": True}, "d": {"accuracy": 2}, "e": {}}
    monkeypatch.setattr("web_router._chains.chains", lambda: {"search": {"measured": rows}})
    assert measured_accuracy("search") == {"a": 0.5}
    assert measured_accuracy("extract") == {}
