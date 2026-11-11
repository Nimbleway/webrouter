"""Perplexity answers, over the Agent (Responses) API, and what research shares with them.

:class:`PerplexityAnswerProvider` asks ``POST /v1/responses`` for one answer in
one call. The answer text, its sources and the domain filter are read and built
here for :mod:`web_router.providers.perplexity`'s search and research too.
Install with the ``answer`` extra. Reads ``PERPLEXITY_API_KEY`` unless a key is
passed explicitly.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import (
    ENV_PERPLEXITY_API_KEY,
    PERPLEXITY_API_KEY_HEADER,
    PERPLEXITY_API_KEY_PREFIX,
    PERPLEXITY_BASE_URL,
    PERPLEXITY_INCOMPLETE_STATUS,
    PERPLEXITY_RESEARCH_PATH,
    PROVIDER_PERPLEXITY,
)
from web_router.constants_perplexity import (
    MSG_PERPLEXITY_ANSWER_DOMAINS_EXCLUSIVE,
    MSG_PERPLEXITY_ANSWER_STOPPED,
    PERPLEXITY_ANSWER_COUNTRY_FIELD,
    PERPLEXITY_ANSWER_DOMAIN_FIELD,
    PERPLEXITY_ANSWER_EFFORT,
    PERPLEXITY_ANSWER_EFFORT_FIELD,
    PERPLEXITY_ANSWER_EXCLUDE_PREFIX,
    PERPLEXITY_ANSWER_LOCATION_FIELD,
    PERPLEXITY_ANSWER_RECENCY,
    PERPLEXITY_ANSWER_RECENCY_FIELD,
    PERPLEXITY_ANSWER_STOPPED,
    PERPLEXITY_ANSWER_TOOL,
    PERPLEXITY_OUTPUT_FETCH_URL_RESULTS,
    PERPLEXITY_OUTPUT_MESSAGE,
    PERPLEXITY_OUTPUT_SEARCH_RESULTS,
    PERPLEXITY_OUTPUT_TEXT,
    PERPLEXITY_SOURCE_LISTS,
)
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpAnswerProvider
from web_router.providers._utils import build_payload, compact, error_detail, mapped
from web_router.providers.answer import cited_marker, refuse_domain_pair
from web_router.types import (
    Citation,
    SearchRequest,
)
from web_router.types_answer import AnswerRequest, AnswerResponse

_MAPPED_SOURCE_FIELDS = frozenset({"url", "title", "snippet"})


def domain_filter(request: SearchRequest | AnswerRequest) -> list[str] | None:
    """Both domain lists as Perplexity's one filter, an excluded domain prefixed; ``None`` when neither is set."""
    excluded = (PERPLEXITY_ANSWER_EXCLUDE_PREFIX + domain for domain in request.exclude_domains or [])
    return [*(request.include_domains or []), *excluded] or None


class PerplexityAnswerProvider(HttpAnswerProvider):
    """Answers a question in one call on Perplexity's Agent API, at its ``fast`` effort.

    The same ``POST /v1/responses`` research uses, without ``background``, so
    the call returns the finished response. The answer cites its sources inline
    as ``[2]``, the ``id`` of a ``search_results`` entry; a citation whose
    marker appears in the answer carries it in ``field``, and one the answer
    never cites has ``None``. A response Perplexity reports ``incomplete`` is
    ``partial``. The response states its own cost under ``usage.cost``, kept
    in ``raw``.

    The filters go to the ``web_search`` tool, configured only when one is set:
    a domain list is ``search_domain_filter`` (an excluded domain prefixed
    ``-``; the two lists together are refused before the call), ``recency`` is ``search_recency_filter`` and ``country``
    is ``user_location.country``. ``locale`` has no field and is refused.
    Another effort level goes through ``provider_options``.
    """

    name: ClassVar[str] = PROVIDER_PERPLEXITY
    supported_parameters: ClassVar[frozenset[str]] = frozenset(
        {"include_domains", "exclude_domains", "recency", "country"}
    )

    base_url: ClassVar[str] = PERPLEXITY_BASE_URL
    answer_path: ClassVar[str] = PERPLEXITY_RESEARCH_PATH
    env_api_key: ClassVar[str] = ENV_PERPLEXITY_API_KEY
    api_key_header: ClassVar[str] = PERPLEXITY_API_KEY_HEADER
    api_key_prefix: ClassVar[str] = PERPLEXITY_API_KEY_PREFIX

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        refuse_domain_pair(self.name, request, MSG_PERPLEXITY_ANSWER_DOMAINS_EXCLUSIVE)
        tool = self._web_search(request)
        return build_payload(
            request,
            input=request.query,
            tools=[tool] if tool else None,
            **{PERPLEXITY_ANSWER_EFFORT_FIELD: PERPLEXITY_ANSWER_EFFORT},
        )

    @staticmethod
    def _web_search(request: AnswerRequest) -> dict[str, Any] | None:
        """The ``web_search`` tool carrying the request's filters, or ``None`` when it sets none."""
        filters = compact(
            {
                PERPLEXITY_ANSWER_DOMAIN_FIELD: domain_filter(request),
                PERPLEXITY_ANSWER_RECENCY_FIELD: mapped(PERPLEXITY_ANSWER_RECENCY, request.recency),
            }
        )
        location = {PERPLEXITY_ANSWER_COUNTRY_FIELD: request.country} if request.country else None
        tool = compact({"filters": filters or None, PERPLEXITY_ANSWER_LOCATION_FIELD: location})
        return {"type": PERPLEXITY_ANSWER_TOOL, **tool} if tool else None

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        status = payload.get("status")
        if status in PERPLEXITY_ANSWER_STOPPED:
            detail = error_detail(payload.get("error")) or status
            raise ProviderError(self.name, MSG_PERPLEXITY_ANSWER_STOPPED.format(status=status, detail=detail))
        output = payload.get("output") or []
        answer = output_text(output)
        return AnswerResponse(
            provider=self.name,
            query=request.query,
            answer=answer,
            citations=output_citations(output, cited_in=answer),
            outcome="partial" if payload.get("status") == PERPLEXITY_INCOMPLETE_STATUS else "complete",
            request_id=payload.get("id"),
            raw=[payload],
        )


def output_text(output: list[dict[str, Any]]) -> str:
    """The written answer: every ``output_text`` part of the ``message`` output, joined."""
    return "".join(
        part.get("text") or ""
        for item in output
        if item.get("type") == PERPLEXITY_OUTPUT_MESSAGE
        for part in item.get("content") or []
        if part.get("type") == PERPLEXITY_OUTPUT_TEXT
    )


def output_citations(output: list[dict[str, Any]], *, cited_in: str | None = None) -> list[Citation]:
    """The sources behind the answer: the pages its searches found, then the ones it opened that none found.

    With ``cited_in``, a search result whose ``[id]`` marker appears in that
    text carries the marker in ``field``. An opened page has no ``id``, so one
    a search already found adds nothing and is left out.
    """
    found = [
        result for item in output if item.get("type") == PERPLEXITY_OUTPUT_SEARCH_RESULTS for result in _pages(item)
    ]
    known = {result.get("url", "") for result in found}
    opened: list[dict[str, Any]] = []
    for item in output:
        if item.get("type") != PERPLEXITY_OUTPUT_FETCH_URL_RESULTS:
            continue
        for page in _pages(item):
            if (url := page.get("url", "")) not in known:
                known.add(url)
                opened.append(page)
    return [
        Citation(
            url=result.get("url", ""),
            title=result.get("title") or "",
            snippet=result.get("snippet") or "",
            field=cited_marker(cited_in, result.get("id")) if cited_in else None,
            extra={key: value for key, value in result.items() if key not in _MAPPED_SOURCE_FIELDS},
        )
        for result in [*found, *opened]
    ]


def _pages(item: dict[str, Any]) -> list[dict[str, Any]]:
    """The pages an output item lists, under the field its type keeps them in."""
    return list(item.get(PERPLEXITY_SOURCE_LISTS[item["type"]]) or [])
