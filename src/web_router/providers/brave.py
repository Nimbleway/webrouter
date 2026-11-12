"""Brave Search provider, over the Web Search API.

Brave publishes no Python SDK, so this talks to the REST endpoints directly.
Install with the ``search`` extra. Reads ``BRAVE_SEARCH_API_KEY`` unless a key
is passed explicitly. :class:`BraveAnswerProvider`, over Brave's Answers API,
lives in :mod:`web_router.providers.brave_answer` and is re-exported here.

Search maps every shared option but ``effort``, which Brave has no tier for.
``search_depth`` serves ``lite`` alone: the endpoint returns a description and
at most a few extra snippets, never a page body. Joined they ran to 2,000
characters live, and Brave has no size setting, so ``lite`` is cut to a snippet
here; unset, the content is every snippet Brave returned. The domain filters travel in
the query as ``site:`` operators, which Brave marks experimental. Everything else
is reachable through ``provider_options``.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from web_router.constants import (
    ENV_BRAVE_API_KEY,
    PROVIDER_BRAVE,
)
from web_router.constants_brave import (
    BRAVE_API_KEY_HEADER,
    BRAVE_BASE_URL,
    BRAVE_COUNTRIES,
    BRAVE_EXCLUDE_DOMAIN,
    BRAVE_FRESHNESS,
    BRAVE_HIGHLIGHT_TAGS,
    BRAVE_QUERY_MAX_CHARS,
    BRAVE_QUERY_MAX_WORDS,
    BRAVE_SEARCH_PARAM_NAMES,
    BRAVE_SEARCH_PARAMETERS,
    BRAVE_SEARCH_PATH,
    MSG_BRAVE_QUERY_TOO_LONG,
    MSG_BRAVE_SEARCH_VALUES,
)
from web_router.constants_search import MSG_SITE_DOMAINS
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpSearchProvider
from web_router.providers._language import language_tag, off_the_lists
from web_router.providers._search_text import lite_body, site_operators, unsited_domains
from web_router.providers._utils import build_payload, mapped
from web_router.providers.brave_answer import BraveAnswerProvider as BraveAnswerProvider
from web_router.types import SearchDepth, SearchRequest, SearchResponse, SearchResult

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "description", "extra_snippets", "page_age"})


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
    supported_parameters: ClassVar[frozenset[str]] = BRAVE_SEARCH_PARAMETERS
    # A description and at most five extra snippets, cut to a snippet for lite: never a body or a page.
    depth_levels: ClassVar[frozenset[SearchDepth]] = frozenset({"lite"})
    refusal_reasons: ClassVar[dict[str, str]] = {
        **dict.fromkeys(("country", "locale"), MSG_BRAVE_SEARCH_VALUES),
        **dict.fromkeys(("include_domains", "exclude_domains"), MSG_SITE_DOMAINS),
    }

    base_url: ClassVar[str] = BRAVE_BASE_URL
    search_path: ClassVar[str] = BRAVE_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_BRAVE_API_KEY
    api_key_header: ClassVar[str] = BRAVE_API_KEY_HEADER
    http_method: ClassVar[Literal["GET", "POST"]] = "GET"

    @classmethod
    def refused_options(cls, request: SearchRequest) -> dict[str, Any]:
        return {**off_the_lists(request, BRAVE_COUNTRIES), **unsited_domains(request)}

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        query = self._query(request)
        words = len(query.split())
        if len(query) > BRAVE_QUERY_MAX_CHARS or words > BRAVE_QUERY_MAX_WORDS:
            raise ProviderError(
                self.name,
                MSG_BRAVE_QUERY_TOO_LONG.format(
                    chars=BRAVE_QUERY_MAX_CHARS, words=BRAVE_QUERY_MAX_WORDS, length=len(query), count=words
                ),
            )
        language = language_tag(request.locale) if request.locale else None
        return build_payload(
            request,
            BRAVE_SEARCH_PARAM_NAMES,
            q=query,
            country=request.country.upper() if request.country else None,
            search_lang=language.lower() if language else None,
            freshness=mapped(BRAVE_FRESHNESS, request.recency),
        )

    @staticmethod
    def _query(request: SearchRequest) -> str:
        operators = site_operators(
            request.include_domains, request.exclude_domains, exclude_format=BRAVE_EXCLUDE_DOMAIN
        )
        return " ".join([request.query, *operators])

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        hits = (payload.get("web") or {}).get("results") or []
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit, request) for hit in hits],
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any], request: SearchRequest) -> SearchResult:
        description = _unmark(hit.get("description") or "")
        # Brave has no full-text mode; the extra snippets are the most body it offers.
        snippets = [_unmark(snippet) for snippet in hit.get("extra_snippets") or []]
        return SearchResult(
            url=hit.get("url", ""),
            title=_unmark(hit.get("title") or ""),
            description=description,
            content=lite_body("\n\n".join([description, *snippets]).strip(), request.search_depth),
            published_date=hit.get("page_age"),
            extra={k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS},
        )
