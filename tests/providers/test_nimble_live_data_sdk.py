"""The Nimble live data provider through the SDK client itself, and live.

The payloads are modelled on the API's response shapes: the run envelope on
a recorded ``extract/templates/run`` answer, and the catalogue items on the
SDK's generated ``TemplateListResponse`` / ``TemplateGetResponse`` models
plus the fields a recorded listing carried that the models do not declare
(``privacy``, ``managed_by``). Names, ids and timestamps are synthetic.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from tests.providers._nimble_live_data_payloads import (
    RECORD,
    TEMPLATE,
    URL,
    _item,
    _run_payload,
    _version,
)
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.types_live_data import LiveDataRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from nimble_python import AsyncNimble, Nimble

from web_router.providers.nimble_live_data import NimbleLiveDataProvider


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleLiveDataProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleLiveDataProvider()


# -- Through the SDK client ---------------------------------------------------


class _Api:
    """A fake of the three template endpoints behind the SDK, answering as the API does and recording each request."""

    def __init__(self, *, missing: str | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.missing = missing

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/extract/templates/run"):
            return httpx.Response(200, json=_run_payload(RECORD))
        if path.endswith("/extract/templates"):
            return httpx.Response(200, json={"items": [_item(_version())], "total": 1, "limit": 20, "offset": 5})
        name = path.rsplit("/", 1)[-1]
        if name == self.missing:
            return httpx.Response(404, json={"detail": f"template {name} not found"})
        return httpx.Response(200, json=_item(_version(name=name), name=name))

    def sync_provider(self, monkeypatch: pytest.MonkeyPatch) -> NimbleLiveDataProvider:
        """A provider on its own clients, with the sync one routed through this fake API."""
        monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
        provider = NimbleLiveDataProvider()
        transport = httpx.MockTransport(self.handle)
        provider._client = Nimble(api_key="test-key", http_client=httpx.Client(transport=transport))
        return provider

    def async_provider(self) -> NimbleLiveDataProvider:
        """A provider on a caller's async client, as an app holding one per end user builds it."""
        transport = httpx.MockTransport(self.handle)
        client = AsyncNimble(api_key="end-user-key", http_client=httpx.AsyncClient(transport=transport))
        return NimbleLiveDataProvider(async_client=client)


def test_run_list_and_describe_through_the_sdk_client(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _Api(missing="gone")
    provider = api.sync_provider(monkeypatch)
    try:
        response = provider.run(LiveDataRequest(source=TEMPLATE, inputs={"url": URL}, format="markdown"))
        page = provider.live_data_sources(limit=20, offset=5)
        template = provider.live_data_source(TEMPLATE)
        with pytest.raises(LiveDataSourceNotFoundError, match="'gone'") as caught:
            provider.live_data_source("gone")
    finally:
        provider.close()

    run, listing, describe, missing = api.requests
    assert run.method == "POST" and run.url.path.endswith("/v2/extract/templates/run")
    assert json.loads(run.content) == {"template": TEMPLATE, "params": {"url": URL}, "formats": ["markdown"]}
    assert run.headers["authorization"] == "Bearer test-key"
    assert response.data == RECORD
    assert response.status_code == 200, "the API's float status code is coerced to an int"
    assert response.document is not None and response.document.format == "markdown"

    assert listing.method == "GET" and listing.url.path.endswith("/v2/extract/templates")
    assert dict(listing.url.params) == {"limit": "20", "offset": "5"}
    assert page.names == [TEMPLATE] and (page.total, page.limit, page.offset) == (1, 20, 5)

    assert describe.method == "GET" and describe.url.path.endswith(f"/v2/extract/templates/{TEMPLATE}")
    assert template.name == TEMPLATE and template.published

    assert missing.url.path.endswith("/v2/extract/templates/gone")
    assert caught.value.template == "gone" and isinstance(caught.value, ProviderError)


def test_a_run_on_an_unknown_template_is_the_same_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoSuchTemplateApi(_Api):
        def handle(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(404, json={"detail": "LiveDataSource not found"})

    provider = _NoSuchTemplateApi().sync_provider(monkeypatch)
    try:
        with pytest.raises(LiveDataSourceNotFoundError, match="'gone'"):
            provider.run(LiveDataRequest(source="gone", inputs={"url": URL}))
    finally:
        provider.close()


def test_paging_left_unset_sends_no_query(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _Api()
    provider = api.sync_provider(monkeypatch)
    try:
        provider.live_data_sources()
    finally:
        provider.close()

    assert dict(api.requests[0].url.params) == {}


async def test_the_async_calls_go_out_on_the_caller_s_client_as_its_end_user() -> None:
    api = _Api()
    provider = api.async_provider()
    try:
        response = await provider.arun(LiveDataRequest(source=TEMPLATE, inputs={"url": URL}))
        page = await provider.alive_data_sources(limit=1)
        template = await provider.alive_data_source(TEMPLATE)
    finally:
        await provider.aclose()

    assert response.data == RECORD and response.document is None
    assert page.names == [TEMPLATE]
    assert template.name == TEMPLATE
    assert all(request.headers["authorization"] == "Bearer end-user-key" for request in api.requests)
    sent = json.loads(api.requests[0].content)
    assert sent == {"template": TEMPLATE, "params": {"url": URL}}, "no format asked for, no formats sent"


def test_an_error_page_through_the_sdk_client_is_a_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    class _MissingPageApi(_Api):
        def handle(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json=_run_payload({"title": "404 Error"}, status_code=404.0))

    provider = _MissingPageApi().sync_provider(monkeypatch)
    try:
        with pytest.raises(ProviderError, match="HTTP 404"):
            provider.run(LiveDataRequest(source=TEMPLATE, inputs={"url": URL}))
    finally:
        provider.close()


# -- Live ---------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("NIMBLE_API_KEY"), reason="NIMBLE_API_KEY not set")
def test_live_catalogue_and_run() -> None:
    """One listing, one description and one run, on whichever published template the account's catalogue holds.

    The template and its inputs come from the catalogue itself -- the first
    published template that publishes a sample input -- so nothing about the
    account is written down here. ``NIMBLE_TEMPLATE`` names one instead.
    """
    from web_router import WebRouter

    with WebRouter("nimble") as router:
        page = router.live_data_sources(provider="nimble", limit=50)
        assert page.provider == "nimble"
        assert 0 < len(page) <= 50
        assert page.total is not None and page.total >= len(page)

        name = os.getenv("NIMBLE_TEMPLATE")
        candidates = (
            [router.live_data_source(name, provider="nimble")] if name else [t for t in page.sources if t.published]
        )
        chosen = next(((t, s.input) for t in candidates for s in t.samples if s.input), None)
        if chosen is None:
            pytest.skip("no published template on this account publishes a sample input to run with")
        template, inputs = chosen
        assert template.input_schema is not None

        response = router.live_data(template.name, inputs, provider="nimble", format="markdown")

    assert response.provider == "nimble"
    assert response.data
    assert response.document is not None and response.document.content
    assert response.document.format == "markdown"
