"""Fireworks answer: a WebRouter search finds the pages, and a chat model on Fireworks answers from them.

Unlike the other answer backends, Fireworks does not search: this backend runs
one search on ``sources`` (``auto`` by default), sends the results to the chat
model named by ``model`` or ``FIREWORKS_ANSWER_MODEL``, and asks for JSON with
the answer and a verbatim quote per citation. Every citation must name a page
the search returned and quote it exactly, or the call fails rather than return
a source the model made up. With no results, the answer is ``""`` and Fireworks
is not called.

Configure it as any backend::

    WebRouter(provider_config={"fireworks": {"model": "accounts/fireworks/models/<model>", "sources": "exa"}})
"""

from __future__ import annotations

import json
import os
from typing import Any, ClassVar

from web_router.constants_fireworks import (
    ENV_FIREWORKS_ANSWER_MODEL,
    ENV_FIREWORKS_API_KEY,
    FIREWORKS_ANSWER_INSTRUCTIONS,
    FIREWORKS_ANSWER_PATH,
    FIREWORKS_BASE_URL,
    FIREWORKS_MAX_SOURCE_CHARS,
    FIREWORKS_MAX_SOURCES,
    FIREWORKS_SOURCES,
    MSG_FIREWORKS_BAD_REPLY,
    MSG_FIREWORKS_UNCITED,
    PROVIDER_FIREWORKS,
)
from web_router.exceptions import ConfigurationError
from web_router.providers._http import _HttpProvider
from web_router.providers.answer import AnswerProvider
from web_router.registry import create_provider
from web_router.types import Citation, SearchRequest, SearchResponse, SearchResult
from web_router.types_answer import AnswerRequest, AnswerResponse


class FireworksAnswerProvider(_HttpProvider, AnswerProvider):
    """An answer written by a Fireworks chat model from the pages one search found."""

    name = PROVIDER_FIREWORKS
    base_url = FIREWORKS_BASE_URL
    env_api_key: ClassVar[str | None] = ENV_FIREWORKS_API_KEY
    api_key_prefix: ClassVar[str] = "Bearer "

    def __init__(self, *, model: str | None = None, sources: str = FIREWORKS_SOURCES, **kwargs: Any) -> None:
        """
        Args:
            model: The Fireworks chat model, e.g. ``accounts/fireworks/models/<model>``; falls back to
                ``FIREWORKS_ANSWER_MODEL``.
            sources: The search backend that finds the pages, by name.
            **kwargs: As for any HTTP backend: ``api_key``, ``timeout``, ``base_url``.
        """
        super().__init__(**kwargs)
        self.model = model or os.environ.get(ENV_FIREWORKS_ANSWER_MODEL)
        self.sources = create_provider(sources)

    def _answer(self, request: AnswerRequest) -> AnswerResponse:
        model = self._required_model()
        found = self.sources.search(self._search_request(request))
        if not found.results:
            return self._unanswered(request, found)
        body = self._body(model, request, found)
        reply = self._client.request("POST", FIREWORKS_ANSWER_PATH, json=body, **self._clamped())
        return self._to_response(request, found, self._payload(reply))

    async def _aanswer(self, request: AnswerRequest) -> AnswerResponse:
        model = self._required_model()
        found = await self.sources.asearch(self._search_request(request))
        if not found.results:
            return self._unanswered(request, found)
        body = self._body(model, request, found)
        reply = await self._async_client.request("POST", FIREWORKS_ANSWER_PATH, json=body, **self._clamped())
        return self._to_response(request, found, self._payload(reply))

    def _required_model(self) -> str:
        """The chat model, checked before the search so a call that cannot answer bills nothing."""
        if not self.model:
            raise ConfigurationError.missing_setting(self.name, env_var=ENV_FIREWORKS_ANSWER_MODEL, setting="model")
        return self.model

    @staticmethod
    def _search_request(request: AnswerRequest) -> SearchRequest:
        return SearchRequest(query=request.query, max_results=FIREWORKS_MAX_SOURCES)

    @staticmethod
    def _body(model: str, request: AnswerRequest, found: SearchResponse) -> dict[str, Any]:
        sources = [{"url": r.url, "title": r.title, "text": _text(r)} for r in found.results]
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": FIREWORKS_ANSWER_INSTRUCTIONS},
                {"role": "user", "content": json.dumps({"question": request.query, "sources": sources})},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }

    def _to_response(self, request: AnswerRequest, found: SearchResponse, payload: dict[str, Any]) -> AnswerResponse:
        try:
            reply = json.loads(payload["choices"][0]["message"]["content"])
            answer, cited = str(reply["answer"]), list(reply["citations"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(MSG_FIREWORKS_BAD_REPLY) from exc
        by_url = {r.url: r for r in found.results}
        citations = [_checked(c, by_url) for c in cited]
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=answer,
            citations=citations,
            request_id=payload.get("id"),
            raw=[found.model_dump(mode="json"), payload],
        )

    def _unanswered(self, request: AnswerRequest, found: SearchResponse) -> AnswerResponse:
        return AnswerResponse(provider=self.name, query=request.query, answer="", raw=[found.model_dump(mode="json")])

    def close(self) -> None:
        self.sources.close()
        super().close()

    async def aclose(self) -> None:
        await self.sources.aclose()
        await super().aclose()


def _text(result: SearchResult) -> str:
    return (result.content or result.description)[:FIREWORKS_MAX_SOURCE_CHARS]


def _checked(cited: Any, by_url: dict[str, SearchResult]) -> Citation:
    """The citation, once its page is one the search returned and its quote is in that page's text as sent."""
    url, quote = str(cited.get("url", "")), " ".join(str(cited.get("quote", "")).split())
    page = by_url.get(url)
    if page is None or not quote or quote not in " ".join(_text(page).split()):
        raise ValueError(MSG_FIREWORKS_UNCITED.format(url=url))
    return Citation(url=url, title=page.title, snippet=quote)
