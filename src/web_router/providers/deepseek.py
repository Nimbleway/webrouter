"""DeepSeek providers, over the Search and Fetch APIs.

Search and extract. Talks to the REST endpoints directly: DeepSeek's Python
route is the OpenAI SDK, which covers its models and not these tools. Install
with the ``search`` or ``extract`` extra. Both read ``DEEPSEEK_API_KEY`` unless a
key is passed explicitly.

Search filters by domain and language through operators in the query rather
than fields, so ``include_domains``, ``exclude_domains`` and ``locale`` are
appended to it, and the whole query must fit DeepSeek's 128 characters.
``need_content``, ``ori_question`` and ``localization`` are reachable through
``provider_options``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from web_router.constants import ENV_DEEPSEEK_API_KEY, MSG_EXTRACT_UNSUCCESSFUL, PROVIDER_DEEPSEEK
from web_router.constants_deepseek import (
    DEEPSEEK_API_KEY_PREFIX,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_EXCLUDE_DOMAIN,
    DEEPSEEK_FETCH_INTERVAL,
    DEEPSEEK_FETCH_PATH,
    DEEPSEEK_INCLUDE_DOMAIN,
    DEEPSEEK_INCLUDE_JOINER,
    DEEPSEEK_LANGUAGE,
    DEEPSEEK_MAX_COUNT,
    DEEPSEEK_QUERY_LIMIT,
    DEEPSEEK_SEARCH_PARAMETERS,
    DEEPSEEK_SEARCH_PATH,
    MSG_DEEPSEEK_QUERY_TOO_LONG,
)
from web_router.exceptions import ProviderError
from web_router.providers._http import HttpExtractProvider, HttpSearchProvider
from web_router.providers._utils import as_snippet, best_text, build_payload
from web_router.types import (
    EXTRACT_FORMAT_ONLY,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

_MAPPED_RESULT_FIELDS = frozenset({"url", "title", "snippet", "content", "rerank_score", "date"})
_MAPPED_DOCUMENT_FIELDS = frozenset({"title", "content"})


class DeepSeekProvider(HttpSearchProvider):
    """Routes searches to the DeepSeek Search API.

    ``score`` is DeepSeek's ``rerank_score``, on its own 0-3 scale (1.5 and up
    it calls high quality). Each result's model-written ``summary`` is its
    content unless the page body was asked for, and stays in ``extra`` either
    way, beside ``authority`` and the site's name and icon.
    """

    name: ClassVar[str] = PROVIDER_DEEPSEEK
    supported_parameters: ClassVar[frozenset[str]] = DEEPSEEK_SEARCH_PARAMETERS

    base_url: ClassVar[str] = DEEPSEEK_BASE_URL
    search_path: ClassVar[str] = DEEPSEEK_SEARCH_PATH
    env_api_key: ClassVar[str] = ENV_DEEPSEEK_API_KEY
    api_key_prefix: ClassVar[str] = DEEPSEEK_API_KEY_PREFIX

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        query = self._query(request)
        if len(query) > DEEPSEEK_QUERY_LIMIT:
            raise ProviderError(
                self.name, MSG_DEEPSEEK_QUERY_TOO_LONG.format(limit=DEEPSEEK_QUERY_LIMIT, length=len(query))
            )
        return build_payload(
            request,
            query=query,
            count=min(request.max_results, DEEPSEEK_MAX_COUNT) if request.max_results else None,
        )

    @staticmethod
    def _query(request: SearchRequest) -> str:
        operators = []
        if request.include_domains:
            operators.append(
                DEEPSEEK_INCLUDE_JOINER.join(DEEPSEEK_INCLUDE_DOMAIN.format(domain=d) for d in request.include_domains)
            )
        operators.extend(DEEPSEEK_EXCLUDE_DOMAIN.format(domain=d) for d in request.exclude_domains or ())
        if request.locale:
            # lang: takes bare language codes, so en-US and en_us both send lang:en.
            language = request.locale.replace("_", "-").split("-")[0].lower()
            operators.append(DEEPSEEK_LANGUAGE.format(language=language))
        return " ".join([request.query, *operators])

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=[self._to_result(hit) for hit in payload.get("results") or []],
            raw=payload,
        )

    @staticmethod
    def _to_result(hit: dict[str, Any]) -> SearchResult:
        snippet = hit.get("snippet") or ""
        summary = hit.get("summary") or ""
        score = hit.get("rerank_score")
        # Documented as RFC 3339 or null; anything else would fail the whole search, so it stays in extra.
        published = hit.get("date")
        extra = {k: v for k, v in hit.items() if k not in _MAPPED_RESULT_FIELDS}
        if published is not None and not isinstance(published, str):
            extra["date"] = published
        return SearchResult(
            url=hit.get("url", ""),
            title=hit.get("title") or "",
            description=snippet or as_snippet(summary),
            content=best_text(hit.get("content"), summary or snippet),
            score=float(score) if isinstance(score, (int, float)) else None,
            published_date=published if isinstance(published, str) and published else None,
            extra=extra,
        )


class DeepSeekExtractProvider(HttpExtractProvider):
    """Fetches page contents through the DeepSeek Fetch API, as markdown.

    A page DeepSeek refuses (403) or cannot read (404) answers with an HTTP
    error, which the base class records as that URL's failure. Fetches start at
    most 5 a second, DeepSeek's per-account limit.
    """

    name: ClassVar[str] = PROVIDER_DEEPSEEK
    supported_parameters: ClassVar[frozenset[str]] = EXTRACT_FORMAT_ONLY
    supported_formats: ClassVar[frozenset[ExtractFormat]] = frozenset({"markdown"})
    # Fetch has no format field: it returns markdown alone.
    default_format: ClassVar[ExtractFormat] = "markdown"
    min_start_interval: ClassVar[float] = DEEPSEEK_FETCH_INTERVAL

    base_url: ClassVar[str] = DEEPSEEK_BASE_URL
    extract_path: ClassVar[str] = DEEPSEEK_FETCH_PATH
    env_api_key: ClassVar[str] = ENV_DEEPSEEK_API_KEY
    api_key_prefix: ClassVar[str] = DEEPSEEK_API_KEY_PREFIX

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return build_payload(request, url=urls[0])

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        if not payload.get("content"):
            failure = ExtractFailure(url=urls[0], error=MSG_EXTRACT_UNSUCCESSFUL)
            return ExtractResponse(provider=self.name, failures=[failure], raw=[payload])

        document = ExtractedDocument(
            # Fetch does not echo the URL back, so the document is labelled from the request.
            url=urls[0],
            title=payload.get("title") or "",
            content=payload["content"],
            format=self.format_of(request),
            extra={k: v for k, v in payload.items() if k not in _MAPPED_DOCUMENT_FIELDS},
        )
        return ExtractResponse(provider=self.name, documents=[document], raw=[payload])
