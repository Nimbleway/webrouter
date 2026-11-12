"""Brave's answers, over its Answers API.

:class:`BraveAnswerProvider` streams one answer with its citations from
``POST /res/v1/chat/completions``; :mod:`web_router.providers.brave`, which
searches, re-exports it. Install with the ``answer`` extra. Reads
``BRAVE_ANSWER_API_KEY`` -- Brave sells answers as a separate plan from
search -- unless a key is passed explicitly.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

import httpx

from web_router.constants import (
    ENV_BRAVE_ANSWER_API_KEY,
    MCP_SSE_DATA_PREFIX,
    PROVIDER_BRAVE,
)
from web_router.constants_brave import (
    BRAVE_ANSWER_CITATION_MAPPED,
    BRAVE_ANSWER_MODEL,
    BRAVE_ANSWER_PATH,
    BRAVE_ANSWER_TAGS,
    BRAVE_API_KEY_HEADER,
    BRAVE_BASE_URL,
    BRAVE_FINISH_LENGTH,
    BRAVE_SSE_DONE,
    MSG_BRAVE_ANSWER_STREAM_CUT,
    MSG_BRAVE_ANSWER_STREAM_ERROR,
)
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpAnswerProvider
from web_router.providers._search_text import as_snippet
from web_router.providers._utils import build_payload
from web_router.types import Citation
from web_router.types_answer import AnswerRequest, AnswerResponse

_ANSWER_TAG = re.compile(r"<({names})>(.*?)</\1>".format(names="|".join(BRAVE_ANSWER_TAGS)), re.DOTALL)


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
            if not line.startswith(MCP_SSE_DATA_PREFIX):
                continue
            data = line[len(MCP_SSE_DATA_PREFIX) :].strip()
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
