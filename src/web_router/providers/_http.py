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
with ``extract_path`` in place of ``search_path`` and a list of URLs passed
alongside the request.

A browse backend, against :class:`HttpBrowseProvider`, implements the job's
``_start`` and ``_fetch`` over the request helpers it is given, as a research
backend does against :class:`HttpResearchProvider`.

A browser backend, against :class:`HttpBrowserProvider`, builds the body that
opens a session and reads the session back out of the answer; ending one is a
path and a method it declares.
"""

from __future__ import annotations

import abc
import contextlib
from typing import Any, ClassVar, Literal
from urllib.parse import quote

import httpx

from web_router.constants import (
    BROWSE_HTTP_TIMEOUT,
    BROWSER_SESSION_ID_FIELD,
    DEFAULT_HTTP_TIMEOUT,
    FOLLOW_REDIRECTS,
    HTTP_ERROR_BODY_LIMIT,
    MSG_HTTP_ERROR,
    MSG_REDIRECTS_WITH_CREDENTIAL,
    RESEARCH_HTTP_TIMEOUT,
)
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._utils import compact, job_budget, research_budget, resolve_api_key
from web_router.providers.base import (
    BrowseProvider,
    BrowserProvider,
    ExtractProvider,
    ResearchProvider,
    SearchProvider,
    _BaseProvider,
)
from web_router.types import (
    BrowserRequest,
    BrowserSession,
    ExtractRequest,
    ExtractResponse,
    SearchRequest,
    SearchResponse,
)


class _HttpProvider(_BaseProvider):
    """A provider backed by a plain HTTP endpoint."""

    base_url: ClassVar[str]

    env_api_key: ClassVar[str | None] = None
    """Variable holding this provider's credential, or ``None`` if it needs none.

    A keyless endpoint sends no auth header and never raises for a missing key.
    """

    api_key_header: ClassVar[str] = "Authorization"
    """Header carrying the credential."""

    api_key_prefix: ClassVar[str] = ""
    """Prepended to the key, e.g. ``"Bearer "``."""

    http_method: ClassVar[Literal["GET", "POST"]] = "POST"
    """``POST`` sends the payload as JSON; ``GET`` sends it as query parameters."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            api_key: Falls back to this provider's ``env_api_key`` variable.
                Ignored by a keyless provider, which declares no variable.
            base_url: Override the API host.
            timeout: Request timeout in seconds, applied to both clients.
            **kwargs: Forwarded to the underlying ``httpx`` clients.

        Raises:
            ConfigurationError: if ``follow_redirects=True`` is passed to a
                provider that sends a credential -- see
                :data:`~web_router.constants.FOLLOW_REDIRECTS`.
        """
        key = self._resolve_key(api_key)
        url = base_url or self.base_url
        super().__init__(api_key=key, base_url=url, timeout=timeout, **kwargs)

        # Merge rather than replace: a caller passing headers= must not silently
        # drop the auth header and get an unexplained 401.
        headers = {"Accept": "application/json"}
        if key is not None:
            headers.update(self._auth_headers(key))
        headers.update(kwargs.pop("headers", None) or {})

        follow_redirects = bool(kwargs.pop("follow_redirects", FOLLOW_REDIRECTS))
        if follow_redirects and key is not None:
            raise ConfigurationError(MSG_REDIRECTS_WITH_CREDENTIAL.format(provider=self.name))

        self._client_kwargs: dict[str, Any] = {
            "base_url": url,
            "timeout": timeout,
            "headers": headers,
            # Pinned rather than left to httpx's default, so the credential
            # cannot travel to a host this provider never meant to call.
            "follow_redirects": follow_redirects,
            **compact(kwargs),
        }
        # Built on first use, and only the one actually used: constructing both
        # eagerly left a sync caller holding an open async connection pool that
        # close() had no way to shut down.
        self._sync: httpx.Client | None = None
        self._async: httpx.AsyncClient | None = None

    def _resolve_key(self, api_key: str | None) -> str | None:
        if not self.env_api_key:
            return None
        return resolve_api_key(api_key, env_var=self.env_api_key, provider=self.name)

    def _auth_headers(self, key: str) -> dict[str, str]:
        """Headers that carry the credential. Overridden by providers that do not use one header.

        Whatever this returns, a provider whose key is set is one the redirect
        pin above refuses to relax, so an unusual auth scheme cannot opt out of
        it by building its own header.
        """
        return {self.api_key_header: f"{self.api_key_prefix}{key}"}

    @property
    def _client(self) -> httpx.Client:
        if self._sync is None:
            self._sync = httpx.Client(**self._client_kwargs)
        return self._sync

    @property
    def _async_client(self) -> httpx.AsyncClient:
        if self._async is None:
            self._async = httpx.AsyncClient(**self._client_kwargs)
        return self._async

    def _payload(self, response: httpx.Response) -> dict[str, Any]:
        """Return the decoded body, turning an HTTP error into a message worth reading.

        Everything else -- timeouts, connection failures, bad JSON -- is wrapped
        by the public ``search``/``extract``.
        """
        self._raise_for_status(response)
        body: dict[str, Any] = response.json()
        return body

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Turn an HTTP error into a message worth reading, without decoding a body.

        Separate from :meth:`_payload` for the calls whose success has no body
        to read -- ending a browser session is often a bare 204.
        """
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = MSG_HTTP_ERROR.format(
                status=exc.response.status_code,
                body=exc.response.text[:HTTP_ERROR_BODY_LIMIT],
            )
            raise ProviderError(self.name, detail, original=exc) from exc

    def close(self) -> None:
        # Via the attribute, not the property: closing must never be the thing
        # that constructs a client.
        if self._sync is not None:
            self._sync.close()

    async def aclose(self) -> None:
        if self._async is not None:
            await self._async.aclose()
        # An async caller may still have built a sync client -- through the
        # sync path, or a library that probed it -- and aclose() is the only
        # shutdown they will call.
        self.close()


class HttpSearchProvider(_HttpProvider, SearchProvider):
    """A search backend over a plain HTTP endpoint."""

    search_path: ClassVar[str]

    # -- What subclasses implement -------------------------------------------

    @abc.abstractmethod
    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        """Translate a normalized request into this API's request body or query."""

    @abc.abstractmethod
    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        """Translate this API's response into a normalized one."""

    # -- The HTTP plumbing ---------------------------------------------------

    def _search(self, request: SearchRequest) -> SearchResponse:
        response = self._client.request(self.http_method, self.search_path, **self._call_kwargs(request))
        return self._to_response(request, self._payload(response))

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        response = await self._async_client.request(self.http_method, self.search_path, **self._call_kwargs(request))
        return self._to_response(request, self._payload(response))

    def _call_kwargs(self, request: SearchRequest) -> dict[str, Any]:
        payload = self._build_payload(request)
        return {"json": payload} if self.http_method == "POST" else {"params": payload}


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
        response = self._client.request(self.http_method, self.extract_path, **self._call_kwargs(request, urls))
        return self._to_response(request, urls, self._payload(response))

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        response = await self._async_client.request(
            self.http_method, self.extract_path, **self._call_kwargs(request, urls)
        )
        return self._to_response(request, urls, self._payload(response))

    def _call_kwargs(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        payload = self._build_payload(request, urls)
        return {"json": payload} if self.http_method == "POST" else {"params": payload}


class HttpResearchProvider(_HttpProvider, ResearchProvider):
    """A research backend over a plain HTTP endpoint.

    Thinner than its two siblings on purpose. A search or an extract is one
    request with one shape, so those base classes can own the whole call; a
    research run is two or three requests whose shapes disagree sharply between
    backends -- one endpoint or two, the result beside the status or behind
    another GET -- and forcing them through a common template would cost more
    than it saves for the three backends that exist. What is shared is what is
    genuinely shared: the client, the credential, redirect handling, HTTP error
    mapping and a longer default timeout.
    """

    def __init__(self, *, timeout: float = RESEARCH_HTTP_TIMEOUT, **kwargs: Any) -> None:
        """
        Args:
            timeout: Request timeout in seconds. Longer than the default for the
                other two methods, because on a backend that answers
                synchronously this one request *is* the run.
            **kwargs: As :class:`_HttpProvider`.
        """
        super().__init__(timeout=timeout, **kwargs)
        # Kept beside the client's own: every request clamps down from here to
        # whatever the caller has left, and never up.
        self._research_timeout = timeout

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._payload(self._client.post(path, json=payload, timeout=self._budget()))

    async def _apost(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._payload(await self._async_client.post(path, json=payload, timeout=self._budget()))

    def _get(self, path: str) -> dict[str, Any]:
        return self._payload(self._client.get(path, timeout=self._budget()))

    async def _aget(self, path: str) -> dict[str, Any]:
        return self._payload(await self._async_client.get(path, timeout=self._budget()))

    def _budget(self) -> float:
        """This request's timeout: the caller's remaining research budget, when there is one.

        Without it the client timeout is the only bound, and a backend that
        runs the whole job inside one request would hold a caller who asked for
        thirty seconds for the two minutes that timeout allows.
        """
        return research_budget(self._research_timeout)


class HttpBrowseProvider(_HttpProvider, BrowseProvider):
    """A browse backend over a plain HTTP endpoint.

    As thin as :class:`HttpResearchProvider`, for the same reason: starting,
    polling and stopping a task are three requests whose paths, verbs and shapes
    differ between vendors -- stopping one is a ``PATCH`` on one, a ``PUT`` on
    another, a ``POST`` on a third. What is shared is the client, the
    credential, the redirect pin, HTTP error mapping, a task id that cannot
    rewrite a path, and every request clamped to the caller's remaining wait.
    """

    def __init__(self, *, timeout: float = BROWSE_HTTP_TIMEOUT, **kwargs: Any) -> None:
        """
        Args:
            timeout: Request timeout in seconds. Longer than the search and
                extract default, because on a backend whose task call blocks
                this one request is the whole task.
            **kwargs: As :class:`_HttpProvider`.
        """
        super().__init__(timeout=timeout, **kwargs)
        self._browse_timeout = timeout

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> httpx.Response:
        """Send one request and raise on an HTTP error, for a call whose success may have no body."""
        response = self._client.request(method, path, json=payload, timeout=self._budget())
        self._raise_for_status(response)
        return response

    async def _acall(self, method: str, path: str, payload: dict[str, Any] | None = None) -> httpx.Response:
        response = await self._async_client.request(method, path, json=payload, timeout=self._budget())
        self._raise_for_status(response)
        return response

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._payload(self._call("POST", path, payload))

    async def _apost(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._payload(await self._acall("POST", path, payload))

    def _get(self, path: str) -> dict[str, Any]:
        return self._payload(self._call("GET", path))

    async def _aget(self, path: str) -> dict[str, Any]:
        return self._payload(await self._acall("GET", path))

    def _task_path(self, template: str, job_id: str) -> str:
        """``template`` with the task id in it, quoted so an id cannot point the request elsewhere."""
        return template.format(job_id=quote(job_id, safe=""))

    def _budget(self) -> float:
        """This request's timeout: the caller's remaining wait, when there is one, never more than the client's."""
        return job_budget(self._browse_timeout)


ReleaseMethod = Literal["POST", "PUT", "DELETE"]
"""The verbs a browser backend ends a session with."""


class HttpBrowserProvider(_HttpProvider, BrowserProvider):
    """A browser backend over a plain HTTP endpoint.

    Opening a session is one request with one shape, so this class owns it:
    a subclass builds the body with :meth:`_build_payload` and reads the answer
    with :meth:`_to_session`. Ending one is a path and a method, declared as
    :attr:`release_path` and :attr:`release_method`, because backends disagree
    about the verb -- a ``POST`` to a release action, a ``PUT`` to a stop, a
    ``DELETE`` on the session -- far more than about anything else. A backend
    whose release needs a body declares :attr:`release_body`, and one that
    answers a refused release with a 200 overrides :meth:`_check_release`. A
    backend that can report a session's state implements :meth:`_fetch`
    itself, over :meth:`_get`.
    """

    open_path: ClassVar[str]

    release_path: ClassVar[str]
    """Where a session is ended, with ``{session_id}`` where its id goes."""

    release_method: ClassVar[ReleaseMethod] = "POST"

    release_body: ClassVar[dict[str, Any] | None] = None
    """The JSON body the release call sends, for a backend whose release is an update; none by default."""

    session_id_field: ClassVar[str] = BROWSER_SESSION_ID_FIELD
    """Where the open response carries the session id; read only to release an orphan."""

    # -- What subclasses implement -------------------------------------------

    @abc.abstractmethod
    def _build_payload(self, request: BrowserRequest) -> dict[str, Any]:
        """Translate a normalized request into the body that opens a session."""

    @abc.abstractmethod
    def _to_session(self, request: BrowserRequest, payload: dict[str, Any]) -> BrowserSession:
        """Translate this API's answer into a normalized session."""

    # -- The HTTP plumbing ---------------------------------------------------

    def _open(self, request: BrowserRequest) -> BrowserSession:
        response = self._client.post(self.open_path, json=self._build_payload(request))
        payload = self._payload(response)
        try:
            return self._to_session(request, payload)
        except Exception:
            # The backend opened a session we cannot describe, and it bills
            # from now on. Release it if the id is there to release by; if that
            # fails too, the original error is still the one worth raising.
            orphan = self._orphan_id(payload)
            if orphan is not None:
                with contextlib.suppress(Exception):
                    self._release(orphan)
            raise

    async def _aopen(self, request: BrowserRequest) -> BrowserSession:
        response = await self._async_client.post(self.open_path, json=self._build_payload(request))
        payload = self._payload(response)
        try:
            return self._to_session(request, payload)
        except Exception:
            orphan = self._orphan_id(payload)
            if orphan is not None:
                with contextlib.suppress(Exception):
                    await self._arelease(orphan)
            raise

    def _orphan_id(self, payload: dict[str, Any]) -> str | None:
        session_id = payload.get(self.session_id_field)
        return session_id if isinstance(session_id, str) and session_id else None

    def _check_release(self, response: httpx.Response) -> None:
        """Raise if a release the backend answered with a success status did not end the session.

        Called after the status check. The default accepts, because most
        backends answer a release with nothing more than its status.
        """

    def _release(self, session_id: str) -> None:
        response = self._client.request(self.release_method, self._session_path(session_id), json=self.release_body)
        self._raise_for_status(response)
        self._check_release(response)

    async def _arelease(self, session_id: str) -> None:
        response = await self._async_client.request(
            self.release_method, self._session_path(session_id), json=self.release_body
        )
        self._raise_for_status(response)
        self._check_release(response)

    def _get(self, path: str) -> dict[str, Any]:
        return self._payload(self._client.get(path))

    async def _aget(self, path: str) -> dict[str, Any]:
        return self._payload(await self._async_client.get(path))

    def _session_path(self, session_id: str, template: str | None = None) -> str:
        """``template`` -- :attr:`release_path` by default -- with the session id in it.

        Quoted, because the id is whatever the backend sent: one carrying a
        slash or a ``?`` must not be able to point the request somewhere else.
        """
        return (template or self.release_path).format(session_id=quote(session_id, safe=""))
