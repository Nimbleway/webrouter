"""A batch that read nothing takes its kind from every URL's failure: one dead URL never stops ``auto`` for all."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from web_router.providers.base import _Collected
from web_router.types import ExtractFailure

A, B = "https://a.test/", "https://b.test/"


def _exhausted(*failures: ExtractFailure) -> Any:
    collected = _Collected("p", [failure.url for failure in failures])
    collected.failures = list(failures)
    return collected._exhausted()


def test_one_unresolvable_url_beside_a_503_leaves_the_batch_retryable() -> None:
    error = _exhausted(
        ExtractFailure(url=A, error="nxdomain: name not resolved"),
        ExtractFailure(url=B, error="service unavailable", status_code=503),
    )
    assert error.kind != "target"
    assert error.retryable is True


def test_a_batch_that_agrees_keeps_its_kind() -> None:
    gone = _exhausted(ExtractFailure(url=A, error="nxdomain"), ExtractFailure(url=B, error="nxdomain"))
    down = _exhausted(
        ExtractFailure(url=A, error="x", status_code=503), ExtractFailure(url=B, error="x", status_code=503)
    )
    assert gone.kind == "target"
    assert down.kind == "upstream"


def test_a_sites_own_403_through_nimble_is_no_refused_key(caplog: pytest.LogCaptureFixture) -> None:
    from web_router.providers.nimble import NimbleExtractProvider
    from web_router.routing.attempts import failed
    from web_router.types import ExtractRequest

    payload = {"url": A, "status": "failed", "status_code": 403, "warnings": ["Forbidden"]}
    response = NimbleExtractProvider(api_key="k")._to_response(ExtractRequest(urls=[A]), [A], payload)
    error = _exhausted(*response.failures, ExtractFailure(url=B, error="x", status_code=403))
    assert error.kind != "auth"
    with caplog.at_level(logging.WARNING, logger="web_router"):
        failed({}, "extract", "nimble", error)
    assert "refused its credential" not in caplog.text


def test_a_retry_reads_by_the_exact_url_asked_for() -> None:
    from web_router.providers._auto_extract_chain import _merged
    from web_router.types import ExtractedDocument, ExtractRequest, ExtractResponse

    page, slashed = "https://a.test/p", "https://a.test/p/"
    both = ExtractRequest(urls=[page, slashed])
    first = ExtractResponse(
        provider="one", failures=[ExtractFailure(url=page, error="down"), ExtractFailure(url=slashed, error="down")]
    )
    second = ExtractResponse(
        provider="two",
        documents=[ExtractedDocument(url=page, content="hi")],
        failures=[ExtractFailure(url=slashed, error="blocked")],
    )
    merged = _merged(first, second, [page, slashed], both)
    assert [failure.url for failure in merged.failures] == [slashed]
    assert merged.failures[0].error == "down; then blocked"

    other = ExtractResponse(provider="two", documents=[ExtractedDocument(url="https://b.test/p", content="hi")])
    assert [f.url for f in _merged(first, other, [page, slashed], both).failures] == [page, slashed]
    lone = ExtractResponse(provider="one", failures=[ExtractFailure(url=page, error="down")])
    rewritten = ExtractResponse(provider="two", documents=[ExtractedDocument(url=slashed, content="hi")])
    assert _merged(lone, rewritten, [page], ExtractRequest(urls=[page])).failures == []


@pytest.mark.parametrize("provider", ["apify", "brightdata", "oxylabs"])
def test_an_unknown_live_data_source_is_not_found_and_not_retried(provider: str) -> None:
    from web_router.exceptions_live_data import LiveDataSourceNotFoundError

    error = LiveDataSourceNotFoundError(provider, "no-such-source")
    assert error.kind == "not_found"
    assert error.retryable is False


def test_a_failure_on_retry_replaces_the_kind_and_the_status_together() -> None:
    """An earlier backend's 429 never stays beside a later backend's ``target``: both describe one failure."""
    from web_router.exceptions import DeadlineExceededError, ProviderError
    from web_router.providers._auto_extract_chain import _also_failed
    from web_router.types import ExtractResponse

    first = ExtractResponse(provider="one", failures=[ExtractFailure(url=A, error="slow", status_code=429)])
    late = _also_failed(first, DeadlineExceededError("two", 5.0)).failures[0]
    assert (late.kind, late.status_code) == ("timeout", None)
    gone = _also_failed(first, ProviderError("two", "every backend met the page's 404", kind="target")).failures[0]
    assert (gone.kind, gone.status_code) == ("target", None)


def test_a_rewritten_url_that_could_be_either_of_two_asked_for_clears_neither() -> None:
    from web_router.providers._auto_extract_chain import _merged
    from web_router.types import ExtractedDocument, ExtractRequest, ExtractResponse

    lower, upper = "https://a.test/p", "https://A.test/p"
    first = ExtractResponse(
        provider="one", failures=[ExtractFailure(url=lower, error="down"), ExtractFailure(url=upper, error="down")]
    )
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url="https://a.test/p/", content="hi")])
    assert [
        failure.url for failure in _merged(first, second, [lower, upper], ExtractRequest(urls=[lower, upper])).failures
    ] == [lower, upper]


def test_a_batch_with_mixed_statuses_names_none_of_them() -> None:
    import httpx

    from web_router.exceptions import ProviderError

    def refused(status: int) -> ProviderError:
        request = httpx.Request("GET", "https://api.test")
        response = httpx.Response(status, request=request)
        return ProviderError("p", "x", original=httpx.HTTPStatusError("x", request=request, response=response))

    collected = _Collected("p", [A, B])
    collected.failures = [
        ExtractFailure(url=A, error="x", status_code=401),
        ExtractFailure(url=B, error="y", status_code=429),
    ]
    collected.errors = [([A], refused(401)), ([B], refused(429))]
    assert collected._exhausted().status_code is None

    collected.failures = [
        ExtractFailure(url=A, error="x", status_code=503),
        ExtractFailure(url=B, error="y", status_code=503),
    ]
    collected.errors = [([A], refused(503)), ([B], refused(503))]
    assert collected._exhausted().status_code == 503


def test_a_gone_page_echoed_without_its_fragment_is_still_gone() -> None:
    from web_router.providers._error_pages import split_error_pages
    from web_router.types import ExtractedDocument

    _, failures = split_error_pages([ExtractedDocument(url=A, status_code=404)], asked=[A + "#top"])
    assert failures[0].kind == "target"
    _, failures = split_error_pages([ExtractedDocument(url=A, status_code=404)], asked=["https://A.TEST/"])
    assert failures[0].kind == "target"


def test_a_rewrite_on_retry_matches_only_the_urls_it_was_sent() -> None:
    from web_router.providers._auto_extract_chain import _merged
    from web_router.types import ExtractedDocument, ExtractRequest, ExtractResponse

    page, slashed = "https://a.test/p", "https://a.test/p/"
    first = ExtractResponse(
        provider="one",
        documents=[ExtractedDocument(url=page, content="hi")],
        failures=[ExtractFailure(url=slashed, error="down")],
    )
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url=page, content="hi again")])
    assert _merged(first, second, [page, slashed], ExtractRequest(urls=[page, slashed])).failures == []


def test_a_url_a_later_member_also_failed_carries_that_members_kind_and_status() -> None:
    from web_router.providers._auto_extract_chain import _merged
    from web_router.types import ExtractRequest, ExtractResponse

    first = ExtractResponse(provider="one", failures=[ExtractFailure(url=A, error="slow", status_code=429)])
    second = ExtractResponse(provider="two", failures=[ExtractFailure(url=A, error="blocked", kind="upstream")])
    failure = _merged(first, second, [A], ExtractRequest(urls=[A])).failures[0]
    assert (failure.kind, failure.status_code) == ("upstream", None)
