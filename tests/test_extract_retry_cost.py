"""A batch ``auto`` reads on two backends answers, and logs, with both backends' cost. Offline.

The test backends take one URL per call and state each call's cost, so a call's cost is the sum of its calls'.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import FakeExtractProvider
from web_router import WebRouter
from web_router.registry import register_extract_provider, unregister_extract_provider
from web_router.spend import SpendEntry
from web_router.types import ExtractFailure, ExtractRequest, ExtractResponse

A, B = "https://a.example/", "https://b.example/"


class _Refuses(FakeExtractProvider):
    """Reads every URL but B, and states its cost."""

    name = "refuses"
    refused = (B,)

    def _respond(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        answer = super()._respond(request, [url for url in urls if url not in self.refused])
        failures = [ExtractFailure(url=url, error="blocked") for url in urls if url in self.refused]
        return answer.model_copy(update={"failures": failures, "cost_usd": 0.002, "cost_source": "reported"})


class _Reads(FakeExtractProvider):
    name = "reads"

    def _respond(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        answer = super()._respond(request, urls)
        return answer.model_copy(update={"cost_usd": 0.001, "cost_source": "reported"})


class _Sink:
    def __init__(self) -> None:
        self.entries: list[SpendEntry] = []

    def record(self, entry: SpendEntry) -> None:
        self.entries.append(entry)


@pytest.fixture(autouse=True)
def _members() -> Any:
    register_extract_provider("refuses", _Refuses)
    register_extract_provider("reads", _Reads)
    _Refuses.refused = (B,)
    yield
    unregister_extract_provider("refuses")
    unregister_extract_provider("reads")


def _extracted(urls: list[str]) -> tuple[ExtractResponse, list[SpendEntry]]:
    sink = _Sink()
    with WebRouter(provider_config={"auto": {"chain": ["refuses", "reads"]}}) as router:
        router.log_spend(sink)
        response = router.extract(urls)
    return response, sink.entries


def test_a_retried_batch_answers_and_logs_both_backends_cost() -> None:
    """Two calls on ``refuses`` (A read, B refused) and one on ``reads`` (B): 0.002 + 0.002 + 0.001."""
    response, entries = _extracted([A, B])

    assert [document.url for document in response.documents] == [A, B]
    assert (response.cost_usd, response.cost_source) == (0.005, "reported")
    assert [entry.cost_usd for entry in entries] == [0.005]


def test_a_batch_with_no_retry_keeps_the_one_backends_cost() -> None:
    _Refuses.refused = ()
    response, entries = _extracted([A, B])

    assert (response.cost_usd, response.cost_source) == (0.004, "reported")
    assert [entry.cost_usd for entry in entries] == [0.004]
