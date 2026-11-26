"""Oxylabs extract on a Web API key, and which of its two APIs serves.

The endpoint, Bearer auth and response shape were verified live on 2026-11-26:
example.com in markdown and HTML, its status under ``metadata.statuses``, and a
target answering 404 reported by the API as a 500.
"""

from __future__ import annotations

import json

import httpx
import pytest

from web_router import WebRouter
from web_router.exceptions import ConfigurationError
from web_router.providers.oxylabs import (
    OxylabsExtractProvider,
    OxylabsRealtimeExtractProvider,
    OxylabsWebApiExtractProvider,
)
from web_router.registry import configured_extract_providers
from web_router.types import ExtractRequest

URL = "https://example.com"

ANSWER = {
    "state": "done",
    "results": [
        {
            "html": None,
            "markdown": "# Example Domain",
            "metadata": {
                "page": 1,
                "run_js": False,
                "url": "https://example.com/",
                "statuses": {"http_request": {"code": 200, "tag": "HTTP_OK"}},
            },
        }
    ],
}


@pytest.fixture(autouse=True)
def only_a_web_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OXYLABS_USERNAME", "OXYLABS_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OXYLABS_WEB_API_KEY", "test-key")


def test_a_web_api_key_alone_fetches_through_the_web_api() -> None:
    sent: list[httpx.Request] = []
    provider = OxylabsExtractProvider(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json=ANSWER))
    )

    response = provider.extract(ExtractRequest(urls=[URL], format="markdown"))

    assert isinstance(provider.api, OxylabsWebApiExtractProvider)
    assert sent[0].url == "https://webapi.oxylabs.io/v1/scrape"
    assert sent[0].headers["authorization"] == "Bearer test-key"
    assert json.loads(sent[0].content) == {"url": URL, "output": ["markdown"]}
    document = response.documents[0]
    assert (document.url, document.content, document.format, document.status_code) == (
        "https://example.com/",
        "# Example Domain",
        "markdown",
        200,
    )


def test_html_asks_for_html_and_reads_it_back() -> None:
    provider = OxylabsExtractProvider()
    answer = {"results": [{"html": "<html>", "markdown": None, "metadata": {"url": URL}}]}
    request = ExtractRequest(urls=[URL], format="html")

    assert provider._build_payload(request, [URL]) == {"url": URL, "output": ["html"]}
    assert provider.api._to_response(request, [URL], answer).documents[0].content == "<html>"


def test_render_mode_runs_javascript() -> None:
    payload = OxylabsExtractProvider()._build_payload(ExtractRequest(urls=[URL], mode="oxylabs-render"), [URL])

    assert payload["run_js"] is True


def test_a_web_api_key_in_code_serves_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OXYLABS_WEB_API_KEY")

    assert isinstance(OxylabsExtractProvider(web_api_key="in-code").api, OxylabsWebApiExtractProvider)


def test_the_realtime_credentials_win_when_both_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OXYLABS_USERNAME", "test-user")
    monkeypatch.setenv("OXYLABS_PASSWORD", "test-pass")

    assert isinstance(OxylabsExtractProvider().api, OxylabsRealtimeExtractProvider)


def test_no_credential_names_both_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OXYLABS_WEB_API_KEY")

    with pytest.raises(ConfigurationError, match=r"OXYLABS_USERNAME.*OXYLABS_WEB_API_KEY"):
        OxylabsExtractProvider()


def test_a_web_api_key_makes_oxylabs_a_configured_extract_backend() -> None:
    assert "oxylabs" in configured_extract_providers()


def test_a_web_api_key_in_code_joins_the_auto_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OXYLABS_WEB_API_KEY")

    with WebRouter(provider_config={"oxylabs": {"web_api_key": "in-code"}}) as router:
        assert "oxylabs" in router.get_extract_provider("auto").members  # type: ignore[attr-defined]
