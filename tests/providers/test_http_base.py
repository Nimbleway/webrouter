"""Tests for the HTTP provider base itself, which every REST-backed provider shares.

Driven through `HttpSearchProvider`, but the plumbing lives one level down on
the base it shares with `HttpExtractProvider`, so an extract backend inherits
exactly what is checked here.

The per-provider tests cover payload and response mapping. What is checked here
is the plumbing underneath them -- credential handling, redirect policy and
client lifetime -- which no single provider owns and all of them inherit.
"""

from __future__ import annotations

import gc
from typing import Any, ClassVar

import httpx
import pytest

from web_router.constants import BROWSE_HTTP_TIMEOUT
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._http import HttpBrowseProvider, HttpBrowserProvider, HttpSearchProvider
from web_router.types import (
    BrowseProgress,
    BrowseRequest,
    BrowseResponse,
    BrowserRequest,
    BrowserSession,
    JobState,
    SearchRequest,
    SearchResponse,
)

ENV_VAR = "ACME_API_KEY"


class AcmeProvider(HttpSearchProvider):
    """A minimal keyed provider, standing in for every REST-backed one."""

    name: ClassVar[str] = "acme"
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"max_results"})

    base_url: ClassVar[str] = "https://api.acme.test"
    search_path: ClassVar[str] = "/v1/search"
    env_api_key: ClassVar[str | None] = ENV_VAR
    api_key_header: ClassVar[str] = "X-API-Key"

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return {"q": request.query}

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(provider=self.name, query=request.query, raw=payload)


class KeylessProvider(AcmeProvider):
    name: ClassVar[str] = "acme-keyless"
    env_api_key: ClassVar[str | None] = None


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> AcmeProvider:
    monkeypatch.setenv(ENV_VAR, "secret-key")
    return AcmeProvider()


# -- Credentials --------------------------------------------------------------


def test_the_key_is_sent_in_the_header_the_provider_declares(keyed: AcmeProvider) -> None:
    assert keyed._client.headers["X-API-Key"] == "secret-key"


def test_a_keyless_provider_sends_no_auth_header_and_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert "X-API-Key" not in KeylessProvider()._client.headers


def test_caller_headers_do_not_displace_the_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing headers= merges; dropping the credential would surface as an unexplained 401."""
    monkeypatch.setenv(ENV_VAR, "secret-key")
    provider = AcmeProvider(headers={"User-Agent": "mine"})

    assert provider._client.headers["X-API-Key"] == "secret-key"
    assert provider._client.headers["User-Agent"] == "mine"


# -- Redirects ----------------------------------------------------------------
# httpx replays request headers to the redirect target and does not strip them
# when that target is another host, so a followed redirect is a credential leak.


def test_redirects_are_off_by_default_rather_than_left_to_httpx(keyed: AcmeProvider) -> None:
    assert keyed._client.follow_redirects is False
    assert keyed._async_client.follow_redirects is False


def test_a_keyed_provider_refuses_to_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "secret-key")
    with pytest.raises(ConfigurationError, match="follow_redirects"):
        AcmeProvider(follow_redirects=True)


def test_a_keyless_provider_may_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no credential to leak, so the caller's choice stands."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert KeylessProvider(follow_redirects=True)._client.follow_redirects is True


# -- Client lifetime ----------------------------------------------------------


def test_neither_client_is_built_until_it_is_used(keyed: AcmeProvider) -> None:
    """Building both eagerly left a sync caller holding an async pool close() could not shut."""
    assert keyed._sync is None
    assert keyed._async is None


def test_touching_one_client_does_not_build_the_other(keyed: AcmeProvider) -> None:
    assert isinstance(keyed._client, httpx.Client)
    assert keyed._async is None


def test_close_is_safe_before_any_client_exists(keyed: AcmeProvider) -> None:
    keyed.close()
    assert keyed._sync is None


def test_close_shuts_the_sync_client(keyed: AcmeProvider) -> None:
    client = keyed._client
    keyed.close()
    assert client.is_closed


async def test_aclose_shuts_both_clients(keyed: AcmeProvider) -> None:
    """An async caller that also used the sync client gets both closed by the one call."""
    sync_client, async_client = keyed._client, keyed._async_client

    await keyed.aclose()

    assert sync_client.is_closed
    assert async_client.is_closed


# -- Browser sessions ---------------------------------------------------------
# Opening one is a POST this base owns; ending one is a path and a verb the
# subclass declares. Both ride the same client, so they inherit everything above.


class AcmeBrowser(HttpBrowserProvider):
    name: ClassVar[str] = "acme-browser"
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"timeout"})

    base_url: ClassVar[str] = "https://browser.acme.test"
    open_path: ClassVar[str] = "/v1/sessions"
    release_path: ClassVar[str] = "/v1/sessions/{session_id}"
    release_method = "DELETE"
    env_api_key: ClassVar[str | None] = ENV_VAR
    api_key_header: ClassVar[str] = "X-API-Key"

    def _build_payload(self, request: BrowserRequest) -> dict[str, Any]:
        return {"lifetime": request.timeout}

    def _to_session(self, request: BrowserRequest, payload: dict[str, Any]) -> BrowserSession:
        return BrowserSession(provider=self.name, session_id=payload["id"], cdp_url=payload["cdp"], raw=payload)


def _browser(monkeypatch: pytest.MonkeyPatch, handler: Any) -> AcmeBrowser:
    monkeypatch.setenv(ENV_VAR, "secret-key")
    return AcmeBrowser(transport=httpx.MockTransport(handler))


def test_a_session_is_opened_and_released_on_the_paths_the_provider_declares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"id": "s-1", "cdp": "wss://cdp.acme.test/s-1"})
        return httpx.Response(204)

    with _browser(monkeypatch, handler).open(BrowserRequest()) as browser:
        assert browser.cdp_url == "wss://cdp.acme.test/s-1"

    assert seen == [("POST", "/v1/sessions"), ("DELETE", "/v1/sessions/s-1")], "a bare 204 is a release"


def test_a_session_id_cannot_rewrite_the_release_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The id is whatever the backend sent; it is data in the path, never more path."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode())
        return httpx.Response(204)

    _browser(monkeypatch, handler).release("../admin?all=1")

    assert paths == ["/v1/sessions/..%2Fadmin%3Fall%3D1"]


def test_a_release_the_backend_refuses_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """This backend cannot report state, so a refused release cannot be assumed to be an expired session."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "s-1", "cdp": "wss://cdp.acme.test/s-1"})
        return httpx.Response(500, text="upstream exploded")

    browser = _browser(monkeypatch, handler).open(BrowserRequest())
    with pytest.raises(ProviderError, match="HTTP 500") as caught:
        browser.release()
    assert isinstance(caught.value.__cause__, httpx.HTTPStatusError), "the cause is the HTTP error, not itself"
    del caught
    with pytest.warns(ResourceWarning, match="garbage-collected"):
        del browser
        gc.collect()


def test_a_browser_provider_refuses_to_follow_redirects_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "secret-key")
    with pytest.raises(ConfigurationError, match="follow_redirects"):
        AcmeBrowser(follow_redirects=True)


def test_a_refused_open_carries_the_http_error_as_its_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    with pytest.raises(ProviderError, match="HTTP 401") as caught:
        _browser(monkeypatch, handler).open(BrowserRequest())

    assert isinstance(caught.value.__cause__, httpx.HTTPStatusError)
    assert caught.value.original is caught.value.__cause__


def _opens_undescribable_session(released: list[str], release_status: int = 204) -> Any:
    """A backend that opens `s-9` but answers without the CDP URL the mapping needs."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "s-9"})
        released.append(request.url.path)
        return httpx.Response(release_status)

    return handler


def test_a_session_the_mapping_cannot_describe_is_released_not_orphaned(monkeypatch: pytest.MonkeyPatch) -> None:
    """It was opened and bills from now on, whether or not we could read the answer."""
    released: list[str] = []

    with pytest.raises(ProviderError, match="cdp"):
        _browser(monkeypatch, _opens_undescribable_session(released)).open(BrowserRequest())

    assert released == ["/v1/sessions/s-9"]


async def test_the_async_path_releases_an_undescribable_session_too(monkeypatch: pytest.MonkeyPatch) -> None:
    released: list[str] = []

    with pytest.raises(ProviderError, match="cdp"):
        await _browser(monkeypatch, _opens_undescribable_session(released)).aopen(BrowserRequest())

    assert released == ["/v1/sessions/s-9"]


def test_a_failed_orphan_release_does_not_mask_the_original_error(monkeypatch: pytest.MonkeyPatch) -> None:
    released: list[str] = []

    with pytest.raises(ProviderError, match="cdp") as caught:
        _browser(monkeypatch, _opens_undescribable_session(released, release_status=500)).open(BrowserRequest())

    assert released == ["/v1/sessions/s-9"]
    assert isinstance(caught.value.__cause__, KeyError), "the mapping's own failure is what is raised"


# -- Browse tasks ---------------------------------------------------------------
# A task is started, polled and stopped with three requests whose verbs differ by
# vendor, so the base hands a provider request helpers rather than owning the
# calls. The helpers ride the same client as everything above.


class AcmeBrowse(HttpBrowseProvider):
    name: ClassVar[str] = "acme-browse"
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"start_url"})

    base_url: ClassVar[str] = "https://tasks.acme.test"
    env_api_key: ClassVar[str | None] = ENV_VAR
    api_key_header: ClassVar[str] = "X-API-Key"

    states: ClassVar[dict[str, JobState]] = {"running": "running", "done": "completed"}

    def _progress(self, request: BrowseRequest, payload: dict[str, Any]) -> BrowseProgress:
        state = self.state_of(payload["status"], self.states)
        response = None
        if state == "completed":
            response = BrowseResponse(provider=self.name, task=request.task, answer=payload["output"], raw=[payload])
        return BrowseProgress(job_id=payload["id"], state=state, response=response)

    def _start(self, request: BrowseRequest) -> BrowseProgress:
        return self._progress(request, self._post("/v1/tasks", {"task": request.task, "url": request.start_url}))

    async def _astart(self, request: BrowseRequest) -> BrowseProgress:
        return self._progress(request, await self._apost("/v1/tasks", {"task": request.task}))

    def _fetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
        return self._progress(request, self._get(self._task_path("/v1/tasks/{job_id}", job_id)))

    async def _afetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
        return self._progress(request, await self._aget(self._task_path("/v1/tasks/{job_id}", job_id)))

    def _cancel(self, job_id: str) -> bool:
        self._call("PATCH", self._task_path("/v1/tasks/{job_id}", job_id), {"action": "stop"})
        return True


def _browse(monkeypatch: pytest.MonkeyPatch, handler: Any) -> AcmeBrowse:
    monkeypatch.setenv(ENV_VAR, "secret-key")
    return AcmeBrowse(transport=httpx.MockTransport(handler))


def _task_backend(seen: list[tuple[str, str, str | None]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode(), request.headers.get("X-API-Key")))
        if request.method == "PATCH":
            return httpx.Response(204)
        status = "running" if request.method == "POST" else "done"
        return httpx.Response(200, json={"id": "t-1", "status": status, "output": "found it"})

    return handler


def test_a_task_is_started_polled_and_stopped_with_the_key_on_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, str | None]] = []
    provider = _browse(monkeypatch, _task_backend(seen))

    job = provider.submit(BrowseRequest(task="find it"))
    assert job.cancel() is True, "a bare 204 is a stop"
    assert job.status().response is not None

    assert seen == [
        ("POST", "/v1/tasks", "secret-key"),
        ("PATCH", "/v1/tasks/t-1", "secret-key"),
        ("GET", "/v1/tasks/t-1", "secret-key"),
    ]


async def test_the_async_task_path_rides_the_same_plumbing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, str | None]] = []
    provider = _browse(monkeypatch, _task_backend(seen))

    job = await provider.asubmit(BrowseRequest(task="find it"))
    response = await job.aresult()
    await provider.aclose()

    assert response.answer == "found it"
    assert [method for method, _, _ in seen] == ["POST", "GET"]


def test_a_task_id_cannot_rewrite_the_poll_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, str | None]] = []
    provider = _browse(monkeypatch, _task_backend(seen))

    provider.poll(BrowseRequest(task="find it"), "../admin?all=1")

    assert seen[0][1] == "/v1/tasks/..%2Fadmin%3Fall%3D1"


def test_a_refused_task_carries_the_http_error_as_its_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text="out of credit")

    with pytest.raises(ProviderError, match="HTTP 402") as caught:
        _browse(monkeypatch, handler).submit(BrowseRequest(task="find it"))

    assert isinstance(caught.value.__cause__, httpx.HTTPStatusError)


def test_a_browse_provider_refuses_to_follow_redirects_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "secret-key")
    with pytest.raises(ConfigurationError, match="follow_redirects"):
        AcmeBrowse(follow_redirects=True)


def test_a_browse_provider_builds_no_client_until_it_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "secret-key")
    provider = AcmeBrowse()

    assert provider._sync is None and provider._async is None
    provider.close()


def test_each_task_request_is_clamped_to_what_the_caller_has_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blocking task call is the whole task, so its HTTP timeout must not outlast the caller's wait."""
    timeouts: list[float | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"id": "t-1", "status": "done", "output": "found it"})

    provider = _browse(monkeypatch, handler)
    provider.browse(BrowseRequest(task="find it"), timeout=5)
    provider.browse(BrowseRequest(task="find it"), timeout=None)

    assert timeouts[0] == pytest.approx(5, abs=0.5)
    assert timeouts[1] == BROWSE_HTTP_TIMEOUT


def _answer_backend(method: str, status: int = 200, body: dict[str, Any] | None = None) -> tuple[Any, list[Any]]:
    """An HTTP answer backend on ``method`` whose API answers ``status`` with ``body``, and the requests it got."""
    import httpx as _httpx

    from web_router.providers._http import HttpAnswerProvider
    from web_router.types_answer import AnswerRequest, AnswerResponse

    seen: list[_httpx.Request] = []

    class Backend(HttpAnswerProvider):
        name = "answer-backend"
        base_url = "https://answer.example"
        answer_path = "/answer"
        http_method = method  # type: ignore[assignment]

        def _build_payload(self, request: AnswerRequest) -> dict[str, str]:
            return {"q": request.query}

        def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
            return AnswerResponse(provider=self.name, query=request.query, answer=str(payload["answer"]))

    def handle(request: _httpx.Request) -> _httpx.Response:
        seen.append(request)
        return _httpx.Response(status, json=body if body is not None else {"answer": "yes"})

    return Backend(transport=_httpx.MockTransport(handle)), seen


def test_an_answer_backend_on_get_sends_its_payload_as_query_parameters() -> None:
    from web_router.types_answer import AnswerRequest

    provider, seen = _answer_backend("GET")

    assert provider.answer(AnswerRequest(query="is it")).answer == "yes"
    assert seen[0].method == "GET" and seen[0].url.params["q"] == "is it" and not seen[0].content


async def test_an_answer_backend_on_post_sends_its_payload_as_a_json_body() -> None:
    import json as _json

    from web_router.types_answer import AnswerRequest

    provider, seen = _answer_backend("POST")

    assert provider.answer(AnswerRequest(query="is it")).answer == "yes"
    assert (await provider.aanswer(AnswerRequest(query="is it"))).answer == "yes"
    for request in seen:
        assert request.method == "POST" and not request.url.params
        assert _json.loads(request.content) == {"q": "is it"}


@pytest.mark.parametrize("status", [400, 503])
def test_an_answer_backends_error_status_is_a_provider_error_naming_it(status: int) -> None:
    from web_router.types_answer import AnswerRequest

    provider, _ = _answer_backend("POST", status, {"error": "refused"})

    with pytest.raises(ProviderError, match=str(status)):
        provider.answer(AnswerRequest(query="is it"))
