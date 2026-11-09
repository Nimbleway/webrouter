"""Guards on the extract contract itself.

The counterpart of test_contract.py. These catch drift that is invisible at
runtime until a user hits it: a backend that reports the wrong format for what
it returned, a fan-out that aborts the URLs that worked, a call that comes back
empty instead of saying why.
"""

from __future__ import annotations

import inspect
from typing import Any, get_args

import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.constants import EXTRACT_ENV_BY_PROVIDER
from web_router.exceptions import ProviderError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.base import ExtractProvider
from web_router.registry import (
    available_extract_providers,
    configured_extract_providers,
    get_extract_provider_class,
)
from web_router.types import (
    EXTRACT_ALWAYS_SENT,
    EXTRACT_PARAMETERS,
    ExtractedDocument,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
)

ROUTER_ONLY_ARGUMENTS = {"self", "urls", "provider", "budget"}

FORMATS: tuple[ExtractFormat, ...] = get_args(ExtractFormat)

ENV_VARS = sorted({env_var for env_vars in EXTRACT_ENV_BY_PROVIDER.values() for env_var in env_vars})

URL = "https://example.com"


def _translates(name: str) -> bool:
    """Whether this provider builds a payload of its own.

    `auto` delegates to other providers instead of talking to an API, so the
    payload-shaping guarantees below are enforced on its members. Its own
    behaviour is covered in tests/test_auto.py.
    """
    return hasattr(get_extract_provider_class(name), "_build_payload")


TRANSLATING = [name for name in available_extract_providers() if _translates(name)]


@pytest.fixture
def credentialed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every extract credential set to a placeholder, so providers construct offline."""
    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, "test-key")


def _build(name: str) -> ExtractProvider:
    return get_extract_provider_class(name)()


def _request_shape(provider: ExtractProvider, request: ExtractRequest, urls: list[str]) -> str:
    """Everything that distinguishes one call from another, however the provider sends it.

    The whole call where there is one to inspect, so a format carried in a
    header counts; the body alone for an SDK-backed provider, where the body
    *is* the call.
    """
    build = getattr(provider, "_call_kwargs", None) or provider._build_payload
    return repr(build(request, urls))


@pytest.mark.parametrize("method", [WebRouter.extract, AsyncWebRouter.extract], ids=["sync", "async"])
def test_router_extract_exposes_every_shared_option(method: object) -> None:
    """The two extract signatures restate ExtractRequest's fields, so guard the copies."""
    exposed = set(inspect.signature(method).parameters) - ROUTER_ONLY_ARGUMENTS
    assert exposed == EXTRACT_PARAMETERS | EXTRACT_ALWAYS_SENT - {"urls"}


def test_every_provider_declares_where_its_credentials_come_from() -> None:
    """configured_extract_providers() reads this table, so a gap there hides a provider."""
    assert set(EXTRACT_ENV_BY_PROVIDER) == set(available_extract_providers())


def test_configured_providers_needs_every_variable_a_backend_takes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Oxylabs needs a username as well as a password; half of it is not usable."""
    for env_var in ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("OXYLABS_PASSWORD", "test-key")

    assert "oxylabs" not in configured_extract_providers()

    monkeypatch.setenv("OXYLABS_USERNAME", "test-user")

    assert "oxylabs" in configured_extract_providers()


def test_a_keyless_provider_needs_no_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    assert "jina" in configured_extract_providers()


def test_extract_parameters_tracks_the_model() -> None:
    assert set(ExtractRequest.model_fields) - EXTRACT_ALWAYS_SENT == EXTRACT_PARAMETERS


@pytest.mark.parametrize("name", available_extract_providers())
def test_providers_declare_only_real_options(name: str) -> None:
    """A typo in supported_parameters would silently reject a valid option forever."""
    assert get_extract_provider_class(name).supported_parameters <= EXTRACT_PARAMETERS


@pytest.mark.parametrize("name", available_extract_providers())
def test_providers_declare_only_real_formats(name: str) -> None:
    assert get_extract_provider_class(name).supported_formats <= set(FORMATS)


@pytest.mark.parametrize("name", available_extract_providers())
def test_a_providers_default_format_is_one_it_supports(name: str) -> None:
    """The default is what an unasked-for call reports having returned.

    Declaring one the provider cannot produce would label the content wrongly
    on every call that named no format -- the common case.
    """
    provider_cls = get_extract_provider_class(name)
    assert provider_cls.default_format in provider_cls.supported_formats


@pytest.mark.parametrize("name", available_extract_providers())
def test_public_extract_is_always_the_base_implementation(name: str) -> None:
    """Validation, fan-out and error wrapping live in `ExtractProvider.extract`.

    A provider that overrode `extract` -- directly or through an intermediate
    base like `HttpExtractProvider` -- would bypass all three.
    """
    provider_cls = get_extract_provider_class(name)
    assert provider_cls.extract is ExtractProvider.extract, f"{name} overrides extract() and would skip validation"
    assert provider_cls.aextract is ExtractProvider.aextract, f"{name} overrides aextract()"


@pytest.mark.parametrize("name", available_extract_providers())
def test_providers_are_concrete(name: str) -> None:
    assert not inspect.isabstract(get_extract_provider_class(name))


@pytest.mark.parametrize("name", TRANSLATING)
def test_provider_options_always_reach_the_api(name: str, credentialed: None) -> None:
    """The escape hatch has to work on every provider, including new ones."""
    provider = _build(name)
    try:
        payload = provider._build_payload(ExtractRequest(urls=[URL], provider_options={"x_custom": "sentinel"}), [URL])
    finally:
        provider.close()

    assert payload["x_custom"] == "sentinel"


@pytest.mark.parametrize("name", TRANSLATING)
def test_no_option_is_ever_sent_as_null(name: str, credentialed: None) -> None:
    """An option left unset must not reach the provider as an explicit null."""
    provider = _build(name)
    try:
        payload = provider._build_payload(ExtractRequest(urls=[URL]), [URL])
    finally:
        provider.close()

    assert None not in payload.values()


@pytest.mark.parametrize("name", TRANSLATING)
def test_every_format_a_provider_supports_is_a_distinct_request(name: str, credentialed: None) -> None:
    """Two formats that build the same call are a mapping bug, not a design choice.

    The caller asks for markdown, gets HTML, and nothing fails -- the same
    class of bug `test_every_depth_level_is_a_distinct_request` catches on the
    search side. Compared on the whole call rather than the body, because Jina
    carries the format in a header.
    """
    provider = _build(name)
    try:
        shapes = {
            body_format: _request_shape(provider, ExtractRequest(urls=[URL], format=body_format), [URL])
            for body_format in sorted(provider.supported_formats)
        }
    finally:
        provider.close()

    assert len(set(shapes.values())) == len(shapes), f"{name} builds the same call for two formats: {shapes}"


@pytest.mark.parametrize("name", TRANSLATING)
def test_every_url_reaches_the_payload(name: str, credentialed: None) -> None:
    """Whether a backend batches or not, the URLs it is handed must be the ones it asks for."""
    provider = _build(name)
    urls = [URL, "https://example.org"] if provider.batches_urls else [URL]
    try:
        payload = provider._build_payload(ExtractRequest(urls=urls), urls)
    finally:
        provider.close()

    sent = repr(payload)
    assert all(url in sent for url in urls), f"{name} dropped a URL it was handed: {payload}"


@pytest.mark.parametrize("name", TRANSLATING)
def test_a_format_a_provider_declares_builds_a_request(name: str, credentialed: None) -> None:
    """Every declared format must be mapped, not just accepted by validate()."""
    provider = _build(name)
    try:
        for body_format in provider.supported_formats:
            request = ExtractRequest(urls=[URL], format=body_format)
            provider.validate(request)
            assert provider._build_payload(request, [URL]) is not None
    finally:
        provider.close()


@pytest.mark.parametrize("name", available_extract_providers())
def test_an_unsupported_format_is_rejected_not_substituted(name: str, credentialed: None) -> None:
    """Returning HTML to a caller who asked for markdown is the failure this prevents."""
    provider = _build(name)
    unsupported = set(FORMATS) - provider.supported_formats
    try:
        for body_format in unsupported:
            with pytest.raises(UnsupportedFormatError, match=body_format):
                provider.extract(ExtractRequest(urls=[URL], format=body_format))  # type: ignore[arg-type]
    finally:
        provider.close()


@pytest.mark.parametrize("name", available_extract_providers())
def test_provider_repr_never_leaks_a_credential(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "SEKRIT-0123456789abcdef"
    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, secret)

    provider = get_extract_provider_class(name)(api_key=secret)
    try:
        assert secret not in repr(provider)
        assert secret not in repr(provider.options)
    finally:
        provider.close()


# -- What ExtractProvider guarantees on every backend's behalf ----------------


def _fake(**overrides: Any) -> ExtractProvider:
    """A fake extract backend with one behaviour swapped out."""
    from tests.conftest import FakeExtractProvider

    return type("Swapped", (FakeExtractProvider,), overrides)()


def test_a_backend_that_takes_one_url_is_called_once_per_url() -> None:
    provider = _fake()
    response = provider.extract(ExtractRequest(urls=[URL, "https://example.org"]))

    assert provider.calls == [[URL], ["https://example.org"]]  # type: ignore[attr-defined]
    assert response.urls == [URL, "https://example.org"]
    assert len(response.raw or []) == 2, "every call's payload must be recoverable"


def test_a_backend_that_batches_is_called_once() -> None:
    provider = _fake(batches_urls=True)
    provider.extract(ExtractRequest(urls=[URL, "https://example.org"]))

    assert provider.calls == [[URL, "https://example.org"]]  # type: ignore[attr-defined]


def test_one_url_failing_does_not_take_the_others_with_it() -> None:
    """The whole point of fanning out per URL: partial success stays partial."""

    def _extract(self: Any, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        if urls == ["https://broken.example"]:
            raise ProviderError("fake", "upstream 500")
        return self._respond(request, urls)

    provider = _fake(_extract=_extract)
    response = provider.extract(ExtractRequest(urls=[URL, "https://broken.example"]))

    assert response.urls == [URL]
    assert [failure.url for failure in response.failures] == ["https://broken.example"]
    assert "upstream 500" in response.failures[0].error


def test_a_call_where_nothing_came_back_raises_rather_than_returning_empty() -> None:
    """A bad credential must not look like a page with no text."""

    def _extract(self: Any, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        raise ProviderError("fake", "HTTP 401: bad key")

    provider = _fake(_extract=_extract)
    with pytest.raises(ProviderError, match="HTTP 401: bad key"):
        provider.extract(ExtractRequest(urls=[URL]))


def test_a_single_failed_call_is_reported_as_the_backend_reported_it() -> None:
    """Summarizing one error as "no URL could be extracted" loses the actual cause."""
    original = RuntimeError("connection reset")

    def _extract(self: Any, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        raise original

    provider = _fake(_extract=_extract)
    with pytest.raises(ProviderError, match="connection reset") as caught:
        provider.extract(ExtractRequest(urls=[URL]))

    assert caught.value.original is original


def test_every_url_failing_reports_all_of_them() -> None:
    def _extract(self: Any, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        raise ProviderError("fake", f"refused {urls[0]}")

    provider = _fake(_extract=_extract)
    with pytest.raises(ProviderError) as caught:
        provider.extract(ExtractRequest(urls=[URL, "https://example.org"]))

    message = str(caught.value)
    assert URL in message and "https://example.org" in message


def test_a_router_error_from_a_backend_is_not_turned_into_a_failure() -> None:
    """An UnsupportedParameterError is a caller bug, not a URL that could not be fetched."""

    def _extract(self: Any, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        raise UnsupportedParameterError("fake", ["format"])

    provider = _fake(_extract=_extract)
    with pytest.raises(UnsupportedParameterError):
        provider.extract(ExtractRequest(urls=[URL]))


def test_validation_runs_before_any_url_is_fetched() -> None:
    provider = _fake(supported_formats=frozenset({"markdown"}))
    with pytest.raises(UnsupportedFormatError):
        provider.extract(ExtractRequest(urls=[URL], format="html"))

    assert provider.calls == []  # type: ignore[attr-defined]


def test_documents_report_the_format_that_was_asked_for() -> None:
    provider = _fake()
    response = provider.extract(ExtractRequest(urls=[URL], format="text"))

    assert response.documents[0].format == "text"


def test_with_no_format_asked_for_every_backend_that_can_returns_markdown() -> None:
    """One answer whichever backend serves the call, even where the vendor's own default is HTML."""
    provider = _fake(default_format="html", supported_formats=frozenset({"html", "markdown"}))
    response = provider.extract(ExtractRequest(urls=[URL]))

    assert response.documents[0].format == "markdown"


def test_a_backend_that_cannot_return_markdown_returns_its_own_default_and_says_so() -> None:
    provider = _fake(default_format="text", supported_formats=frozenset({"text"}))
    response = provider.extract(ExtractRequest(urls=[URL]))

    assert response.documents[0].format == "text"


def test_a_format_asked_for_is_still_what_comes_back() -> None:
    provider = _fake(default_format="html", supported_formats=frozenset({"html", "markdown"}))
    response = provider.extract(ExtractRequest(urls=[URL], format="html"))

    assert response.documents[0].format == "html"


async def test_the_async_path_fans_out_and_keeps_the_order() -> None:
    provider = _fake()
    urls = [URL, "https://example.org", "https://example.net"]
    response = await provider.aextract(ExtractRequest(urls=urls))

    assert response.urls == urls, "concurrent calls must still come back in the order asked for"


async def test_the_async_path_reports_partial_failure_the_same_way() -> None:
    async def _aextract(self: Any, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        if urls == ["https://broken.example"]:
            raise ProviderError("fake", "upstream 500")
        return self._respond(request, urls)

    provider = _fake(_aextract=_aextract)
    response = await provider.aextract(ExtractRequest(urls=[URL, "https://broken.example"]))

    assert response.urls == [URL]
    assert [failure.url for failure in response.failures] == ["https://broken.example"]


def test_a_backend_that_answers_with_nothing_in_it_raises() -> None:
    """The one outcome a caller cannot tell from a page with no text."""

    def _extract(self: Any, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        return ExtractResponse(provider="fake", raw=[{}])

    provider = _fake(_extract=_extract)
    with pytest.raises(ProviderError, match="no URL could be extracted") as caught:
        provider.extract(ExtractRequest(urls=[URL]))

    assert URL in str(caught.value), "the error has to name the URL it got nothing for"


# -- What a multi-URL call must hold on every real provider -------------------

TWO_URLS = ["https://a.example", "https://b.example"]


def _answered(provider: ExtractProvider, urls: list[str]) -> ExtractResponse:
    """Run a real provider's own mapping over a stub payload, one call per batch.

    Drives `extract()` rather than `_to_response` directly, so the fan-out is
    part of what is under test -- which is where these guarantees break.
    """
    calls: list[list[str]] = []

    def _extract(request: ExtractRequest, batch: list[str]) -> ExtractResponse:
        calls.append(batch)
        return provider._to_response(request, batch, _STUB_PAYLOADS[provider.name](batch))

    provider._extract = _extract  # type: ignore[method-assign]
    response = provider.extract(ExtractRequest(urls=urls))
    assert calls, "the provider never reached its own mapping"
    return response


def _body(url: str) -> str:
    return f"body of {url}"


_STUB_PAYLOADS: dict[str, Any] = {
    "nimble": lambda urls: {"url": urls[0], "status": "success", "data": {"markdown": _body(urls[0])}},
    "tavily": lambda urls: {"results": [{"url": url, "raw_content": _body(url)} for url in urls]},
    "exa": lambda urls: {"results": [{"id": url, "url": url, "text": _body(url)} for url in urls]},
    "firecrawl": lambda urls: {
        "success": True,
        "data": {"markdown": _body(urls[0]), "metadata": {"sourceURL": urls[0]}},
    },
    "oxylabs": lambda urls: {
        # Asked for markdown -- the default now -- Oxylabs adds a markdown result beside the raw HTML.
        "results": [
            {"url": urls[0], "content": "<html>", "type": "raw"},
            {"url": urls[0], "content": _body(urls[0]), "type": "markdown"},
        ],
    },
    "brightdata": lambda urls: {"status_code": 200, "body": _body(urls[0])},
    "jina": lambda urls: {"data": {"url": urls[0], "content": _body(urls[0])}},
    "linkup": lambda urls: {"markdown": _body(urls[0]), "favicon": "https://favicons.linkup.so?domain=example.com"},
    "webiq": lambda urls: {"url": urls[0], "title": "", "content": _body(urls[0]), "traceId": "t"},
    "keenable": lambda urls: {"url": urls[0], "title": "", "content": _body(urls[0]), "description": ""},
    "deepseek": lambda urls: {"title": "", "content": _body(urls[0])},
    "browserbase": lambda urls: {"statusCode": 200, "content": _body(urls[0]), "contentType": "text/html"},
}
"""The shape each backend really answers with, trimmed to what is mapped.

Keyed by provider so a new backend has to be added here to pass the guarantees
below -- which is the point: they are the ones a single-URL test cannot check.
"""


@pytest.mark.parametrize("name", TRANSLATING)
def test_every_backend_is_stubbed_for_the_multi_url_guarantees(name: str) -> None:
    assert name in _STUB_PAYLOADS, f"add a stub payload for {name} so it is held to the checks below"


@pytest.mark.parametrize("name", sorted(_STUB_PAYLOADS))
def test_each_document_is_labelled_with_the_url_its_own_call_asked_for(name: str, credentialed: None) -> None:
    """A backend called once per URL must not label every document with the first one.

    Bright Data's envelope carries no URL at all (nor does Browserbase Fetch's), so
    before `_to_response` was handed its own batch it labelled document two with URL
    one -- two documents, the same `url`, different bodies, and no way to tell which.
    """
    provider = _build(name)
    try:
        response = _answered(provider, TWO_URLS)
    finally:
        provider.close()

    assert len(response) == 2, f"{name} lost a document: {response.failures}"
    assert response.urls == TWO_URLS
    for url, document in zip(TWO_URLS, response.documents, strict=True):
        assert document.content == _body(url), f"{name} paired {document.url} with the wrong body"


@pytest.mark.parametrize("name", sorted(_STUB_PAYLOADS))
def test_every_requested_url_is_accounted_for(name: str, credentialed: None) -> None:
    """A URL the backend mentions neither as a document nor as a failure must not vanish."""
    provider = _build(name)
    try:
        response = _answered(provider, TWO_URLS)
    finally:
        provider.close()

    accounted = {document.url for document in response.documents} | {failure.url for failure in response.failures}
    assert accounted == set(TWO_URLS), f"{name} did not account for every URL: {accounted}"


@pytest.mark.parametrize("name", sorted(_STUB_PAYLOADS))
def test_the_response_payload_is_recoverable_in_full(name: str, credentialed: None) -> None:
    """`raw` must still hold the body, so normalization stays reversible.

    Nimble's mapping used to `pop` the body out of the payload it then stored,
    which left the content unrecoverable from `raw` at all.
    """
    provider = _build(name)
    try:
        response = _answered(provider, [TWO_URLS[0]])
    finally:
        provider.close()

    assert _body(TWO_URLS[0]) in repr(response.raw), f"{name} mutated the payload it stored as raw"


@pytest.mark.parametrize("name", sorted(_STUB_PAYLOADS))
def test_documents_come_back_in_the_order_the_urls_were_asked_for(name: str, credentialed: None) -> None:
    """Otherwise the order of the answer tells you which backend served it.

    A batching backend answers in whatever order its own API chose; only the
    fan-out is in request order by construction.
    """
    provider = _build(name)
    reversed_urls = list(reversed(TWO_URLS))
    try:
        response = _answered(provider, reversed_urls)
    finally:
        provider.close()

    assert response.urls == reversed_urls, f"{name} reordered the documents"


def test_a_batching_backend_answering_out_of_order_is_reordered() -> None:
    """The case the real backends happen not to exercise, so it is pinned here."""

    def _extract(self: Any, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        return ExtractResponse(
            provider="fake",
            documents=[ExtractedDocument(url=url, content=url) for url in reversed(urls)],
        )

    provider = _fake(batches_urls=True, _extract=_extract)
    response = provider.extract(ExtractRequest(urls=TWO_URLS))

    assert response.urls == TWO_URLS


def test_a_backend_that_rewrites_the_url_keeps_its_own_order() -> None:
    """Reordering must not drop a document just because its URL came back canonicalized."""

    def _extract(self: Any, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        return ExtractResponse(
            provider="fake",
            documents=[ExtractedDocument(url=f"{url}/", content=url) for url in urls],
        )

    provider = _fake(batches_urls=True, _extract=_extract)
    response = provider.extract(ExtractRequest(urls=TWO_URLS))

    assert [document.content for document in response.documents] == TWO_URLS
