"""Brave Search provider, over the Web Search API, and Brave's answers, over its Answers API.

Brave publishes no Python SDK, so this talks to the REST endpoints directly.
Install with the ``search`` extra (the ``answer`` extra for answers). Search
reads ``BRAVE_SEARCH_API_KEY`` and answers ``BRAVE_ANSWER_API_KEY`` -- Brave
sells them as separate plans -- unless a key is passed explicitly.

Slim by design: only ``max_results`` is mapped. ``search_depth`` is absent
because the API has no content-depth control -- it returns a description and a
few extra snippets, and that is all. Everything else is reachable through
``provider_options``; mapping one properly is a welcome PR -- see CONTRIBUTING.md.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar, Literal

import httpx

from web_router.constants import (
    BRAVE_API_KEY_HEADER,
    BRAVE_BASE_URL,
    BRAVE_HIGHLIGHT_TAGS,
    BRAVE_PARAM_NAMES,
    BRAVE_SEARCH_PATH,
    ENV_BRAVE_ANSWER_API_KEY,
    ENV_BRAVE_API_KEY,
    PROVIDER_BRAVE,
)
from web_router.constants_brave import (
    BRAVE_ANSWER_CITATION_MAPPED,
    BRAVE_ANSWER_MODEL,
    BRAVE_ANSWER_PATH,
    BRAVE_ANSWER_TAGS,
    BRAVE_FINISH_LENGTH,
    BRAVE_SSE_DATA_PREFIX,
    BRAVE_SSE_DONE,
    MSG_BRAVE_ANSWER_STREAM_CUT,
    MSG_BRAVE_ANSWER_STREAM_ERROR,
)
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpAnswerProvider, HttpSearchProvider
from web_router.providers._utils import as_snippet, build_payload
from web_router.types import Citation, SearchRequest, SearchResponse, SearchResult
from web_router.types_answer import AnswerRequest, AnswerResponse

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "description", "extra_snippets", "page_age"})

_ANSWER_TAG = re.compile(r"<({names})>(.*?)</\1>".format(names="|".join(BRAVE_ANSWER_TAGS)), re.DOTALL)


def _unmark(text: str) -> str:
    """Strip the ``<strong>`` markers Brave wraps query terms in.

    They are Brave's presentation markup, not part of the page, and every other
    provider returns plain text here.
    """
    for tag in BRAVE_HIGHLIGHT_TAGS:
        text = text.replace(tag, "")
    return text


class BraveProvider(HttpSearchProvider):
    """Routes searches to the Brave Web Search API."""

    name: ClassVar[str] = PROVIDER_BRAVE
    supported_parameters: ClassVar[frozenset[str]] = frozenset(BRAVE_PARAM_NAMES)

    base_url: ClassVar[str] = BRAVE_BASE_URL
    search_path: ClassVar[str] = BRAVE_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_BRAVE_API_KEY
    api_key_header: ClassVar[str] = BRAVE_API_KEY_HEADER
    http_method: ClassVar[Literal["GET", "POST"]] = "GET"

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return build_payload(request, BRAVE_PARAM_NAMES, q=request.query)

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        hits = (payload.get("web") or {}).get("results") or []
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in hits],
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        description = _unmark(hit.get("description") or "")
        # Brave has no full-text mode; the extra snippets are the most body it offers.
        snippets = [_unmark(snippet) for snippet in hit.get("extra_snippets") or []]
        return SearchResult(
            url=hit.get("url", ""),
            title=_unmark(hit.get("title") or ""),
            description=description,
            content="\n\n".join([description, *snippets]).strip(),
            published_date=hit.get("page_age"),
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )


class BraveAnswerProvider(HttpAnswerProvider):
    """Answers a question through Brave's Answers API, ``POST /res/v1/chat/completions``.

    Sent streamed with ``enable_citations``, the only way Brave returns its
    sources, and read whole before answering. The answer is the streamed text
    with Brave's ``<citation>`` and ``<usage>`` tags taken out, as search takes
    out its ``<strong>`` markup; each citation tag becomes a :class:`Citation`
    in order, its ``number`` and its ``start_index`` / ``end_index`` into that
    answer in ``extra``. Brave writes no marker into the text, so ``field`` is
    unset. ``raw`` is the stream's events, untouched, the ``<usage>`` tag and
    its stated cost among them. An answer cut off at its token limit is
    ``partial``.

    No normalized option is mapped: the API has no source filter, and
    ``language`` showed no effect live, so ``locale`` is refused.
    """

    name: ClassVar[str] = PROVIDER_BRAVE
    supported_parameters: ClassVar[frozenset[str]] = frozenset()

    base_url: ClassVar[str] = BRAVE_BASE_URL
    answer_path: ClassVar[str] = BRAVE_ANSWER_PATH
    env_api_key: ClassVar[str] = ENV_BRAVE_ANSWER_API_KEY
    api_key_header: ClassVar[str] = BRAVE_API_KEY_HEADER

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        return build_payload(
            request,
            model=BRAVE_ANSWER_MODEL,
            messages=[{"role": "user", "content": request.query}],
            stream=True,
            enable_citations=True,
        )

    def _payload(self, response: httpx.Response) -> dict[str, Any]:
        """The stream's events in order, each decoded.

        An event that reports an error raises, and so does a stream that ends
        before ``[DONE]``: a connection dropped mid-answer is a failure, not a
        short answer.
        """
        self._raise_for_status(response)
        events: list[dict[str, Any]] = []
        for line in response.text.splitlines():
            if not line.startswith(BRAVE_SSE_DATA_PREFIX):
                continue
            data = line[len(BRAVE_SSE_DATA_PREFIX) :].strip()
            if data == BRAVE_SSE_DONE:
                return {"events": events}
            event = json.loads(data)
            if event.get("error"):
                raise ProviderError(self.name, MSG_BRAVE_ANSWER_STREAM_ERROR.format(error=event["error"]))
            events.append(event)
        raise ProviderError(self.name, MSG_BRAVE_ANSWER_STREAM_CUT.format(count=len(events)))

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        events = payload.get("events") or []
        choices = [choice for event in events for choice in event.get("choices") or []]
        text = "".join((choice.get("delta") or {}).get("content") or "" for choice in choices)
        cited = [json.loads(body) for tag, body in _ANSWER_TAG.findall(text) if tag == "citation"]
        finished = {choice.get("finish_reason") for choice in choices} - {None}
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=_ANSWER_TAG.sub("", text),
            citations=[
                Citation(
                    url=source.get("url", ""),
                    snippet=as_snippet(source.get("snippet") or ""),
                    extra={key: value for key, value in source.items() if key not in BRAVE_ANSWER_CITATION_MAPPED},
                )
                for source in cited
            ],
            outcome="partial" if BRAVE_FINISH_LENGTH in finished else "complete",
            request_id=next((event["id"] for event in events if event.get("id")), None),
            raw=[payload],
        )
