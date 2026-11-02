"""The Parallel answer provider, over a stubbed Chat API.

Verified against the live API on 2026-11-01. The Chat API is OpenAI-compatible
and in beta: `POST /v1beta/chat/completions` with a bearer token. `lite`
answered in about seven seconds with one sentence and a top-level `basis` --
per answer `field`, its `citations` (`title`, `url`, `excerpts`), a `reasoning`
and a `confidence` -- from which the payload below is trimmed. `speed`
answered faster with `basis: []`, its sources only as `[domain, date]` text,
so it is not used.

The Chat API documents no source filters, so every normalized option is
refused. A `finish_reason` of `length` was not seen live; it is read as
`partial` because it is the API's own word for a cut-short generation.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.providers._answer_stub import stub_answer_api
from web_router.exceptions import UnsupportedParameterError
from web_router.providers.parallel import ParallelAnswerProvider
from web_router.types_answer import AnswerRequest

QUESTION = "Who won the 2022 FIFA World Cup final and what was the score?"
FIFA = "https://www.fifa.com/en/match-centre/match/17/255711/285077/400128145"
TEXT = "Argentina won the 2022 FIFA World Cup final, defeating France 4-2 on penalties after a 3-3 draw."


def _answered(finish_reason: str = "stop", text: str = TEXT, field: str = "answer") -> dict[str, Any]:
    return {
        "id": "chatcmpl_c8af9ef0-6a00-4cda-b915-28cfa87adab8",
        "object": "chat.completion",
        "model": "lite",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": text, "annotations": None},
            }
        ],
        "usage": None,
        "basis": [
            {
                "field": field,
                "citations": [
                    {
                        "title": "Argentina vs France 3-3 | Final | FIFA World Cup Qatar 2022",
                        "url": FIFA,
                        "excerpts": ["Argentina vs.", "France 3-3"],
                    }
                ],
                "reasoning": "The official FIFA match listing records a 3-3 score and a 4-2 shootout.",
                "confidence": "high",
            }
        ],
    }


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ParallelAnswerProvider:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    return ParallelAnswerProvider()


def test_an_answer_is_one_chat_completion_with_the_lite_model(provider: ParallelAnswerProvider) -> None:
    api = stub_answer_api(provider, _answered())

    provider.answer(AnswerRequest(query=QUESTION))

    assert api.calls == [("POST", "/v1beta/chat/completions")]
    assert api.bodies == [{"model": "lite", "messages": [{"role": "user", "content": QUESTION}]}]
    assert api.headers[0]["authorization"] == "Bearer test-key"


def test_the_answer_and_its_basis_are_normalized(provider: ParallelAnswerProvider) -> None:
    stub_answer_api(provider, _answered())

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.provider == "parallel"
    assert response.answer == TEXT
    assert response.request_id == "chatcmpl_c8af9ef0-6a00-4cda-b915-28cfa87adab8"
    assert response.outcome == "complete"
    (citation,) = response.citations
    assert citation.url == FIFA
    assert citation.title.startswith("Argentina vs France")
    assert citation.snippet == "Argentina vs.\n\nFrance 3-3"
    assert citation.field is None, "a basis for the whole answer ties the source to no one part"
    assert citation.extra == {"confidence": "high"}
    assert response.raw is not None
    assert response.raw[0]["basis"][0]["reasoning"].startswith("The official FIFA")


def test_a_basis_for_another_field_names_it(provider: ParallelAnswerProvider) -> None:
    stub_answer_api(provider, _answered(field="score"))

    assert provider.answer(AnswerRequest(query=QUESTION)).citations[0].field == "score"


def test_a_generation_cut_short_is_partial(provider: ParallelAnswerProvider) -> None:
    stub_answer_api(provider, _answered(finish_reason="length"))

    assert provider.answer(AnswerRequest(query=QUESTION)).outcome == "partial"


def test_a_filter_is_refused_since_the_chat_api_takes_none(provider: ParallelAnswerProvider) -> None:
    api = stub_answer_api(provider, _answered())

    with pytest.raises(UnsupportedParameterError, match="include_domains"):
        provider.answer(AnswerRequest(query=QUESTION, include_domains=["a.com"]))

    assert api.calls == []


def test_an_empty_answer_is_returned(provider: ParallelAnswerProvider) -> None:
    stub_answer_api(
        provider, {"id": "x", "choices": [{"finish_reason": "stop", "message": {"content": None}}], "basis": []}
    )

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert (response.answer, response.citations) == ("", [])


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("PARALLEL_API_KEY"), reason="PARALLEL_API_KEY not set")
def test_live_answer_comes_with_its_basis() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("parallel") as router, billing_aware("parallel"):
        response = router.answer(QUESTION)

    assert "argentina" in (response.answer or "").lower()
    assert response.citations
    assert all(citation.url.startswith("http") for citation in response.citations)
