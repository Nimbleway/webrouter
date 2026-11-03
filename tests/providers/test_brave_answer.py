"""The Brave answer provider, over a stubbed Answers API.

Verified against the live API on 2026-11-03. `POST /res/v1/chat/completions`
with `model: brave`, `stream: true` and `enable_citations: true` answered in
about 2.4 seconds as `chat.completion.chunk` events. The text carries a
`<citation>{...}</citation>` tag after each sentence a source backs -- 22 of
them for this question, several per sentence -- and a closing `<usage>` tag
stating the call's cost ($0.054, almost all of it 9,887 input tokens). A
blocking call answered with no sources. The stream below is trimmed from that
call: its first sentence, two of that sentence's citations, the usage tag and
the closing event.

No normalized option is mapped: the API documents no source filter, and
`language: "es"` answered in English while an unknown value was accepted.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.brave import BraveAnswerProvider
from web_router.types_answer import AnswerRequest

QUESTION = "Who won the 2022 FIFA World Cup final and what was the score?"
SPORTING = "https://www.sportingnews.com/us/soccer/news/who-won-2022-fifa-world-cup-final-score-result-highlights-qatar"
CBS = "https://www.cbs8.com/article/sports/soccer/world-cup/lionel-messi-argentina-fifa-world-cup-2022-champion"
SENTENCE = "**Argentina** won the 2022 FIFA World Cup, defeating **France** in the final."


def _citation(number: int, url: str, snippet: str) -> str:
    tag = {
        "start_index": 77,
        "end_index": 77,
        "number": number,
        "url": url,
        "favicon": "https://imgs.example/f",
        "snippet": snippet,
    }
    return f"<citation>{json.dumps(tag)}</citation>"


USAGE = '<usage>{"X-Request-Queries": 1, "X-Request-Tokens-In": 9887, "X-Request-Total-Cost": 0.054205}</usage>'
CONTENT = [
    "*",
    "*Arg",
    "entina** won the",
    " 2022 FIFA World Cup, defeating **France** in the final.",
    _citation(1, SPORTING, "**Final score: Argentina 3, France 3 (Argentina won 4-2 on penalties)**"),
    _citation(2, CBS, "Argentina won the 2022 FIFA World Cup after defeating France in a dramatic penalty shootout"),
    USAGE,
]


def _stream(content: list[str], finish: str = "stop", request_id: str = "860830f9-9150-4b00-ad39-ab0370bdaf7a") -> str:
    def event(delta: dict[str, Any], finish_reason: str | None) -> str:
        chunk = {"id": request_id, "object": "chat.completion.chunk", "model": "brave-pro"}
        chunk["choices"] = [{"delta": delta, "finish_reason": finish_reason}]
        return "data: " + json.dumps(chunk)

    lines = [event({"role": "assistant", "content": part}, None) for part in content]
    lines.append(event({}, finish))
    return "\n\n".join([*lines, "data: [DONE]", ""])


class StreamApi:
    def __init__(self, body: str, status: int = 200) -> None:
        self.body, self.status = body, status
        self.calls: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(self.status, text=self.body, headers={"content-type": "text/event-stream"})


def _stub(provider: BraveAnswerProvider, body: str, status: int = 200) -> StreamApi:
    api = StreamApi(body, status)
    transport = httpx.MockTransport(api.handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return api


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> BraveAnswerProvider:
    monkeypatch.setenv("BRAVE_ANSWER_API_KEY", "test-key")
    return BraveAnswerProvider()


def test_an_answer_is_one_streamed_post_asking_for_citations(provider: BraveAnswerProvider) -> None:
    api = _stub(provider, _stream(CONTENT))

    provider.answer(AnswerRequest(query=QUESTION))

    (call,) = api.calls
    assert call.url.path == "/res/v1/chat/completions"
    assert call.headers["X-Subscription-Token"] == "test-key"
    assert json.loads(call.content) == {
        "model": "brave",
        "messages": [{"role": "user", "content": QUESTION}],
        "stream": True,
        "enable_citations": True,
    }


def test_the_answer_is_the_streamed_text_without_brave_s_tags(provider: BraveAnswerProvider) -> None:
    _stub(provider, _stream(CONTENT))

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.answer == SENTENCE
    assert response.outcome == "complete"
    assert response.request_id == "860830f9-9150-4b00-ad39-ab0370bdaf7a"
    assert response.raw is not None and "X-Request-Total-Cost" in json.dumps(response.raw), (
        "the stated cost stays in raw"
    )


def test_each_citation_tag_is_a_citation_in_order_with_its_span(provider: BraveAnswerProvider) -> None:
    _stub(provider, _stream(CONTENT))

    citations = provider.answer(AnswerRequest(query=QUESTION)).citations

    assert [c.url for c in citations] == [SPORTING, CBS]
    assert citations[0].snippet.startswith("**Final score")
    assert citations[0].field is None, "Brave writes no marker into the text"
    assert citations[0].extra == {"start_index": 77, "end_index": 77, "number": 1, "favicon": "https://imgs.example/f"}
    assert SENTENCE[: citations[0].extra["end_index"]] == SENTENCE, "the span counts into the answer without tags"


def test_an_answer_cut_off_at_its_token_limit_is_partial(provider: BraveAnswerProvider) -> None:
    _stub(provider, _stream(CONTENT[:3], finish="length"))

    assert provider.answer(AnswerRequest(query=QUESTION)).outcome == "partial"


def test_an_error_event_in_the_stream_is_a_provider_error(provider: BraveAnswerProvider) -> None:
    _stub(provider, 'data: {"error": {"message": "rate limited"}}\n\ndata: [DONE]\n')

    with pytest.raises(ProviderError, match="rate limited"):
        provider.answer(AnswerRequest(query=QUESTION))


def test_an_http_error_names_the_status(provider: BraveAnswerProvider) -> None:
    _stub(provider, '{"error": "OPTION_NOT_IN_PLAN"}', status=402)

    with pytest.raises(ProviderError, match="402"):
        provider.answer(AnswerRequest(query=QUESTION))


@pytest.mark.parametrize("option", [{"country": "US"}, {"locale": "es"}, {"recency": "week"}])
def test_every_option_is_refused_before_sending(provider: BraveAnswerProvider, option: dict[str, Any]) -> None:
    api = _stub(provider, _stream(CONTENT))

    with pytest.raises(UnsupportedParameterError):
        provider.answer(AnswerRequest(query=QUESTION, **option))
    assert api.calls == []


async def test_aanswer_reads_the_stream_the_same_way(provider: BraveAnswerProvider) -> None:
    _stub(provider, _stream(CONTENT))

    response = await provider.aanswer(AnswerRequest(query=QUESTION))

    assert response.answer == SENTENCE and len(response.citations) == 2
