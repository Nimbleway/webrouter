"""LlamaParse provider tests, against a mock of LlamaCloud's Parse API v2.

The shapes follow the API v2 guide and the llama-cloud SDK's ``ParsingGetResponse``; the live test below runs only
with ``LLAMA_CLOUD_API_KEY`` set.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
import pytest

from tests.conftest import FakeExtractProvider
from web_router import WebRouter
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers import llamaparse
from web_router.providers.auto import AutoExtractProvider
from web_router.providers.llamaparse import LlamaParseExtractProvider
from web_router.types import ExtractRequest
from web_router.types_errors import ERROR_KIND_TIMEOUT

URL = "https://example.com/report.pdf"
LIVE_PDF = "https://arxiv.org/pdf/1706.03762v7.pdf"


class LlamaCloud:
    """Answers create, poll and result calls; ``statuses`` is what each poll before the result reports."""

    def __init__(self, statuses: list[str], final: str = "COMPLETED") -> None:
        self.statuses, self.final, self.requests = list(statuses), final, []  # type: list[httpx.Request]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job-1", "status": "PENDING", "project_id": "p"})
        job: dict[str, Any] = {"id": "job-1", "project_id": "p", "status": self.final, "error_message": None}
        expand = request.url.params.get("expand")
        if expand is None:
            job["status"] = self.statuses.pop(0) if self.statuses else self.final
            return httpx.Response(200, json={"job": job})
        if self.final == "FAILED":
            job["error_message"] = "unsupported file"
        return httpx.Response(200, json={"job": job, expand: f"{expand} of the report"})


def _provider(cloud: LlamaCloud) -> LlamaParseExtractProvider:
    return LlamaParseExtractProvider(api_key="test-key", transport=httpx.MockTransport(cloud))


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llamaparse, "LLAMAPARSE_POLL_SECONDS", 0.0)


@pytest.mark.parametrize(("fmt", "field"), [("markdown", "markdown_full"), ("text", "text_full")])
def test_a_url_is_parsed_as_a_job_and_read_in_the_format_asked_for(fmt: str, field: str) -> None:
    cloud = LlamaCloud(["PENDING", "RUNNING"])
    provider = _provider(cloud)
    try:
        response = provider.extract(ExtractRequest(urls=[URL], format=fmt))  # type: ignore[arg-type]
    finally:
        provider.close()

    created = json.loads(cloud.requests[0].content)
    assert created == {"source_url": URL, "tier": "cost_effective", "version": "latest"}
    assert cloud.requests[0].headers["Authorization"] == "Bearer test-key"
    assert cloud.requests[-1].url.params["expand"] == field
    document = response.documents[0]
    assert (document.url, document.format, document.content) == (URL, fmt, f"{field} of the report")
    assert response.request_id == "job-1"


def test_provider_options_override_the_tier() -> None:
    cloud = LlamaCloud([])
    provider = _provider(cloud)
    try:
        provider.extract(ExtractRequest(urls=[URL], provider_options={"tier": "agentic"}))
    finally:
        provider.close()

    assert json.loads(cloud.requests[0].content)["tier"] == "agentic"


def test_a_failed_job_is_a_failure_naming_why() -> None:
    provider = _provider(LlamaCloud([], final="FAILED"))
    try:
        with pytest.raises(ProviderError, match="job-1 ended FAILED: unsupported file"):
            provider.extract(ExtractRequest(urls=[URL]))
    finally:
        provider.close()


def test_a_job_still_running_when_the_wait_runs_out_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llamaparse, "LLAMAPARSE_WAIT_SECONDS", 0.0)
    provider = _provider(LlamaCloud(["RUNNING"] * 5))
    try:
        with pytest.raises(ProviderError, match="job-1 was still RUNNING") as raised:
            provider.extract(ExtractRequest(urls=[URL]))
    finally:
        provider.close()

    assert raised.value.kind == ERROR_KIND_TIMEOUT


def test_the_async_path_waits_the_same_way() -> None:
    cloud = LlamaCloud(["RUNNING"])
    provider = _provider(cloud)

    async def run() -> str:
        try:
            return (await provider.aextract(ExtractRequest(urls=[URL]))).documents[0].content
        finally:
            await provider.aclose()

    assert asyncio.run(run()) == "markdown_full of the report"


def test_bytes_are_uploaded_with_the_job_configuration() -> None:
    cloud = LlamaCloud([])
    provider = _provider(cloud)
    try:
        response = provider.parse_bytes(b"%PDF-1.4 ...", url=URL, format="text")
    finally:
        provider.close()

    upload = cloud.requests[0]
    assert upload.url.path == "/api/v2/parse/upload"
    assert b'filename="report.pdf"' in upload.content and b"%PDF-1.4 ..." in upload.content
    assert b'"tier": "cost_effective"' in upload.content
    assert response.documents[0].content == "text_full of the report"


def test_a_signed_urls_query_is_not_uploaded_as_the_filename() -> None:
    cloud = LlamaCloud([])
    provider = _provider(cloud)
    try:
        provider.parse_bytes(b"%PDF-1.4 ...", url=URL + "?X-Amz-Signature=secret", format="text")
    finally:
        provider.close()

    assert b'filename="report.pdf"' in cloud.requests[0].content
    assert b"secret" not in cloud.requests[0].content


def test_a_poll_that_fails_after_the_job_started_names_the_job() -> None:
    def cloud(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job-1", "status": "PENDING"})
        raise httpx.ConnectError("connection reset", request=request)

    provider = _provider(cloud)  # type: ignore[arg-type]
    try:
        with pytest.raises(ProviderError, match="job-1"):
            provider.extract(ExtractRequest(urls=[URL]))
        with pytest.raises(ProviderError, match="job-1"):
            asyncio.run(provider.aextract(ExtractRequest(urls=[URL])))
    finally:
        provider.close()


def test_a_missing_key_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLAMA_CLOUD_API_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        LlamaParseExtractProvider()


def test_auto_sends_documents_to_llamaparse_when_it_is_the_parser(fake_extract_provider: Any) -> None:
    cloud = LlamaCloud([])
    transport = httpx.MockTransport(cloud)
    members = {"llamaparse": {"api_key": "test-key", "transport": transport}}
    auto = AutoExtractProvider(chain=(("fake", {}),), members_config=members, document_parser="llamaparse")
    try:
        response = auto.extract(ExtractRequest(urls=[URL, "https://example.com/about"]))
        chain = auto._member(0)
    finally:
        auto.close()

    assert response.provider == "llamaparse"
    assert [d.content for d in response.documents] == ["markdown_full of the report", "hello"]
    assert isinstance(chain, FakeExtractProvider) and chain.calls == [["https://example.com/about"]]


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("LLAMA_CLOUD_API_KEY"), reason="LLAMA_CLOUD_API_KEY not set")
def test_a_live_pdf_is_parsed() -> None:
    with WebRouter("llamaparse") as router:
        document = router.extract([LIVE_PDF]).documents[0]

    assert "Attention Is All You Need" in document.content
