"""The Fireworks answer provider, over a stubbed search and a stubbed chat completions API.

Mapped from the Fireworks docs, not yet a live call: chat completions are
OpenAI-shaped, and `response_format: {"type": "json_object"}` asks for JSON.
The search that finds the pages is any WebRouter backend, stubbed here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

from tests.conftest import FakeProvider
from tests.providers._answer_stub import stub_answer_api
from web_router import register_provider, unregister_provider
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers.fireworks import FireworksAnswerProvider
from web_router.types import SearchRequest, SearchResponse, SearchResult
from web_router.types_answer import AnswerRequest

QUESTION = "Where is the Eiffel Tower?"
MODEL = "accounts/fireworks/models/test-model"
PAGE = SearchResult(
    url="https://en.wikipedia.org/wiki/Eiffel_Tower",
    title="Eiffel Tower",
    content="The Eiffel Tower is a wrought-iron lattice tower on the Champ de Mars in Paris, France.",
)


class Pages(FakeProvider):
    """The search under the answer: every query finds ``found``, set per test."""

    name = "pages"
    found: ClassVar[list[SearchResult]] = []

    def _respond(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        return SearchResponse(provider=self.name, query=request.query, results=self.found)


@pytest.fixture(autouse=True)
def _pages() -> Iterator[None]:
    register_provider(Pages.name, Pages)
    yield
    unregister_provider(Pages.name)


def _reply(answer: str, citations: list[dict[str, str]]) -> dict[str, Any]:
    content = json.dumps({"answer": answer, "citations": citations})
    return {"id": "chat-1", "choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def _provider(results: list[SearchResult], *, model: str | None = MODEL) -> FireworksAnswerProvider:
    Pages.found = results
    return FireworksAnswerProvider(api_key="test-key", model=model, sources=Pages.name)


CITED = {"url": PAGE.url, "quote": "on the Champ de Mars   in Paris"}


def test_answers_from_the_pages_found_with_each_quote_checked() -> None:
    provider = _provider([PAGE])
    api = stub_answer_api(provider, _reply("It is in Paris.", [CITED]))

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert api.calls == [("POST", "/inference/v1/chat/completions")]
    body = api.bodies[0]
    assert body["model"] == MODEL
    assert body["response_format"] == {"type": "json_object"}
    sent = json.loads(body["messages"][1]["content"])
    assert sent["sources"][0]["url"] == PAGE.url
    assert api.headers[0]["Authorization"] == "Bearer test-key"
    assert response.answer == "It is in Paris."
    assert [(c.url, c.snippet) for c in response.citations] == [(PAGE.url, "on the Champ de Mars in Paris")]


@pytest.mark.parametrize(
    "citation",
    [{"url": "https://example.com/made-up", "quote": "in Paris"}, {"url": PAGE.url, "quote": "in Lyon"}],
)
def test_a_citation_not_in_the_pages_fails_the_call(citation: dict[str, str]) -> None:
    provider = _provider([PAGE])
    stub_answer_api(provider, _reply("It is in Paris.", [citation]))

    with pytest.raises(ProviderError, match="not in the sources"):
        provider.answer(AnswerRequest(query=QUESTION))


def test_with_no_pages_the_answer_is_empty_and_fireworks_is_not_called() -> None:
    provider = _provider([])
    api = stub_answer_api(provider, _reply("unused", []))

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.answer == ""
    assert api.calls == []


def test_with_no_model_it_fails_before_searching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIREWORKS_ANSWER_MODEL", raising=False)
    provider = _provider([PAGE], model=None)

    with pytest.raises(ConfigurationError, match="FIREWORKS_ANSWER_MODEL"):
        provider.answer(AnswerRequest(query=QUESTION))
    assert isinstance(provider.sources, Pages)
    assert provider.sources.requests == []


async def test_answers_async_too() -> None:
    provider = _provider([PAGE])
    stub_answer_api(provider, _reply("It is in Paris.", [CITED]))

    response = await provider.aanswer(AnswerRequest(query=QUESTION))

    assert response.citations[0].url == PAGE.url


@pytest.mark.integration
@pytest.mark.skipif(
    not (os.getenv("FIREWORKS_API_KEY") and os.getenv("FIREWORKS_ANSWER_MODEL")),
    reason="FIREWORKS_API_KEY and FIREWORKS_ANSWER_MODEL not set",
)
def test_live_answer_cites_the_pages_it_was_given() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter() as router, billing_aware("fireworks"):
        response = router.answer(QUESTION, provider="fireworks")

    assert response.provider == "fireworks"
    assert response.answer
    assert response.citations
