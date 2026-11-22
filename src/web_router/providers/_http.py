"""Base classes for providers that talk to a REST API directly.

Several of these APIs publish no Python SDK, and for the slim usage web-router
makes of them an SDK dependency would buy little. :class:`_HttpProvider` owns
everything those providers share -- client construction, auth headers, HTTP
error mapping, cleanup -- and the two classes built on it leave a provider with
just two methods: build the request, read the response.

To add a search backend::

    class AcmeProvider(HttpSearchProvider):
        name = "acme"
        supported_parameters = frozenset({"max_results"})
        base_url = "https://api.acme.com"
        search_path = "/v1/search"
        env_api_key = "ACME_API_KEY"
        api_key_header = "X-API-Key"

        def _build_payload(self, request): ...
        def _to_response(self, request, payload): ...

An extract backend is the same shape, against :class:`HttpExtractProvider`,
with ``extract_path`` and a list of URLs beside the request; an answer backend,
against :class:`HttpAnswerProvider`, with ``answer_path``.

A browse backend, against :class:`HttpBrowseProvider`, implements the job's
``_start`` and ``_fetch`` over the request helpers it is given, as a research
backend does against :class:`HttpResearchProvider`. A research backend whose
status call carries the finished answer declares its paths and status table
against :class:`HttpPolledResearchProvider` instead, and is left with building
the request and reading the answer.

A browser backend, against :class:`HttpBrowserProvider`, builds the body that
opens a session and reads the session back out of the answer; ending one is a
path and a method it declares.
"""

from __future__ import annotations

import abc
from typing import Any, ClassVar, Generic, TypeVar

from web_router.providers._http_base import _HttpProvider
from web_router.providers._http_browser import HttpBrowserProvider, ReleaseMethod
from web_router.providers._http_jobs import HttpBrowseProvider, HttpPolledResearchProvider, HttpResearchProvider
from web_router.providers.answer import AnswerProvider
from web_router.providers.base import ExtractProvider, SearchProvider
from web_router.types import ExtractRequest, ExtractResponse, SearchRequest, SearchResponse
from web_router.types_answer import AnswerRequest, AnswerResponse

__all__ = [
    "HttpAnswerProvider",
    "HttpBrowseProvider",
    "HttpBrowserProvider",
    "HttpExtractProvider",
    "HttpPolledResearchProvider",
    "HttpResearchProvider",
    "HttpSearchProvider",
    "ReleaseMethod",
    "_HttpProvider",
]

_RequestT = TypeVar("_RequestT", SearchRequest, AnswerRequest)
_ResponseT = TypeVar("_ResponseT", SearchResponse, AnswerResponse)


class _HttpOneCallProvider(_HttpProvider, Generic[_RequestT, _ResponseT]):
    """A backend that answers in one HTTP call; ``path_attribute`` names the method's own path attribute."""

    path_attribute: ClassVar[str]

    # -- What subclasses implement -------------------------------------------

    @abc.abstractmethod
    def _build_payload(self, request: _RequestT) -> dict[str, Any]:
        """Translate a normalized request into this API's request body or query."""

    @abc.abstractmethod
    def _to_response(self, request: _RequestT, payload: dict[str, Any]) -> _ResponseT:
        """Translate this API's response into a normalized one."""

    # -- The HTTP plumbing ---------------------------------------------------

    def _call(self, request: _RequestT) -> _ResponseT:
        path = getattr(self, self.path_attribute)
        kwargs = {**self._call_kwargs(request), **self._clamped()}
        return self._to_response(request, self._payload(self._client.request(self.http_method, path, **kwargs)))

    async def _acall(self, request: _RequestT) -> _ResponseT:
        path = getattr(self, self.path_attribute)
        kwargs = {**self._call_kwargs(request), **self._clamped()}
        response = await self._async_client.request(self.http_method, path, **kwargs)
        return self._to_response(request, self._payload(response))

    def _call_kwargs(self, request: _RequestT) -> dict[str, Any]:
        payload = self._build_payload(request)
        return {"json": payload} if self.http_method == "POST" else {"params": payload}


class HttpSearchProvider(_HttpOneCallProvider[SearchRequest, SearchResponse], SearchProvider):
    """A search backend over a plain HTTP endpoint."""

    search_path: ClassVar[str]
    path_attribute = "search_path"
    _search = _HttpOneCallProvider._call
    _asearch = _HttpOneCallProvider._acall


class HttpAnswerProvider(_HttpOneCallProvider[AnswerRequest, AnswerResponse], AnswerProvider):
    """An answer backend over a plain HTTP endpoint: one call, one answer."""

    answer_path: ClassVar[str]
    path_attribute = "answer_path"
    _answer = _HttpOneCallProvider._call
    _aanswer = _HttpOneCallProvider._acall


class HttpExtractProvider(_HttpProvider, ExtractProvider):
    """An extract backend over a plain HTTP endpoint."""

    extract_path: ClassVar[str]

    # -- What subclasses implement -------------------------------------------

    @abc.abstractmethod
    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        """Translate a normalized request into this API's request body or query.

        ``urls`` is one call's worth -- every URL for an API that batches them,
        a single-item list for one that does not.
        """

    @abc.abstractmethod
    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        """Translate one call's response into a normalized one.

        ``urls`` is this call's own batch, not the whole request: a backend that
        does not echo the URL back must label its document from here, or every
        call after the first would be labelled with the first URL asked for.
        """

    # -- The HTTP plumbing ---------------------------------------------------

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        kwargs = {**self._call_kwargs(request, urls), **self._clamped()}
        response = self._client.request(self.http_method, self.extract_path, **kwargs)
        return self._to_response(request, urls, self._payload(response))

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        kwargs = {**self._call_kwargs(request, urls), **self._clamped()}
        response = await self._async_client.request(self.http_method, self.extract_path, **kwargs)
        return self._to_response(request, urls, self._payload(response))

    def _call_kwargs(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        payload = self._build_payload(request, urls)
        return {"json": payload} if self.http_method == "POST" else {"params": payload}
