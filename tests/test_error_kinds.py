"""Errors a caller branches on by their fields: a backend failure's kind, a refused option, every member failing."""

from __future__ import annotations

import httpx
import pytest

from web_router import AllProvidersFailedError, Budget, InvalidParameterError, WebRouter, WebRouterError
from web_router.exceptions import ProviderError
from web_router.types import ExtractFailure
from web_router.types_errors import classify


def _status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.example.com/search")
    response = httpx.Response(status, headers=headers, request=request)
    return httpx.HTTPStatusError("failed", request=request, response=response)


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (401, "auth", False),
        (403, "auth", False),
        (402, "rate_limit", True),
        (429, "rate_limit", True),
        (400, "bad_request", False),
        (422, "bad_request", False),
        (404, "not_found", False),
        (418, "bad_request", False),
        (503, "upstream", True),
    ],
)
def test_an_http_status_sets_the_kind(status: int, kind: str, retryable: bool) -> None:
    error = ProviderError("acme", f"HTTP {status}", original=_status_error(status))

    assert (error.kind, error.status_code, error.retryable) == (kind, status, retryable)


def test_retry_after_is_read_in_seconds() -> None:
    error = ProviderError("acme", "HTTP 429", original=_status_error(429, {"Retry-After": "7"}))

    assert error.retry_after == 7.0


def test_a_backend_naming_the_page_as_the_problem_is_target_whatever_its_status() -> None:
    assert ProviderError("nimble", "HTTP 400: nx_domain_error", original=_status_error(400)).kind == "target"
    assert classify(None, "ParamValidationError(url): TypeError: Invalid URL").kind == "target"


def test_failing_to_reach_the_backend_itself_is_not_the_page() -> None:
    """glibc says "name or service not known" when the backend's own host does not resolve."""
    reason = httpx.ConnectError("[Errno -2] Name or service not known")

    assert ProviderError("acme", "search failed", original=reason).kind == "upstream"


def test_a_closed_event_loop_is_local_and_says_how_to_fix_it() -> None:
    error = ProviderError("acme", "search failed: Event loop is closed", original=RuntimeError("Event loop is closed"))

    assert error.kind == "local"
    assert "one AsyncWebRouter per loop" in str(error)


def test_a_providers_own_bug_is_local_and_a_timeout_is_a_timeout() -> None:
    assert ProviderError("acme", "bad shape", original=KeyError("results")).kind == "local"
    assert ProviderError("acme", "slow", original=httpx.ReadTimeout("read")).kind == "timeout"
    assert ProviderError("acme", "no results").kind == "upstream"


def test_an_extract_failure_carries_a_kind() -> None:
    assert ExtractFailure(url="u", error="nx_domain_error").kind == "target"
    failure = ExtractFailure.from_error("u", ProviderError("acme", "HTTP 401", original=_status_error(401)))
    assert (failure.kind, failure.status_code, failure.retryable) == ("auth", 401, False)


@pytest.mark.parametrize(
    ("call", "field", "allowed"),
    [
        (lambda router: router.search(""), "query", None),
        (lambda router: router.search("x", max_results=0), "max_results", None),
        (lambda router: router.search("x", search_depth="ultra"), "search_depth", ["lite", "fast", "deep"]),
        (lambda router: router.extract([]), "urls", None),
    ],
)
def test_a_refused_option_is_an_invalid_parameter_error_naming_the_field(call, field, allowed) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(InvalidParameterError) as caught:
        call(WebRouter())

    error = caught.value
    assert isinstance(error, WebRouterError) and isinstance(error, ValueError)
    assert (error.field, error.allowed) == (field, allowed)
    assert "pydantic" not in str(error), "one line, no validator docs link"


def test_a_budget_cap_that_is_not_a_number_is_an_invalid_parameter_error() -> None:
    with pytest.raises(InvalidParameterError) as caught:
        Budget(max_cost_usd=-1)

    assert caught.value.field == "max_cost_usd"


def test_every_member_failing_keeps_each_ones_error_and_their_shared_kind() -> None:
    refused = {name: ProviderError(name, "HTTP 401", original=_status_error(401)) for name in ("tavily", "exa")}
    throttled = {"brave": ProviderError("brave", "HTTP 429", original=_status_error(429))}

    same = AllProvidersFailedError("auto", "every backend failed", refused)
    mixed = AllProvidersFailedError("auto", "every backend failed", {**refused, **throttled})

    assert isinstance(same, ProviderError)
    assert same.failures == refused and same.kind == "auth" and same.retryable is False
    assert mixed.kind == "upstream" and mixed.retryable is True


def test_an_infinite_retry_after_is_no_wait_at_all() -> None:
    error = ProviderError("acme", "HTTP 429", original=_status_error(429, {"Retry-After": "inf"}))

    assert error.retry_after is None


def test_a_wrapped_backend_failure_keeps_its_kind() -> None:
    inner = ProviderError("acme", "HTTP 429", original=_status_error(429))

    assert ProviderError("acme", "the job could not be read", original=inner).kind == "rate_limit"


def test_live_data_refuses_a_bad_option_as_an_invalid_parameter() -> None:
    with pytest.raises(InvalidParameterError) as caught:
        WebRouter().live_data("x", {}, provider="nimble", format="pdf")  # type: ignore[arg-type]

    assert caught.value.field == "format"


def test_a_retry_after_date_without_a_zone_is_read_as_utc() -> None:
    error = ProviderError("acme", "HTTP 503", original=_status_error(503, {"Retry-After": "Wed, 21 Oct 2015 07:28:00"}))

    assert error.retry_after == 0.0, "a date in the past is no wait, and reading it raised nothing"
    assert error.kind == "upstream"


def test_a_refused_key_is_auth_whatever_its_body_says() -> None:
    error = ProviderError("acme", "HTTP 401: invalid url or key", original=_status_error(401))

    assert error.kind == "auth"


def test_a_timeout_carried_as_the_cause_is_a_timeout() -> None:
    class APIConnectionError(Exception):
        pass

    try:
        try:
            raise httpx.ConnectTimeout("connect")
        except httpx.ConnectTimeout as inner:
            raise APIConnectionError("Connection error.") from inner
    except APIConnectionError as outer:
        wrapped = outer

    assert ProviderError("acme", "search failed", original=wrapped).kind == "timeout"


def test_a_failure_with_a_gone_status_is_target_even_when_its_text_is_bare() -> None:
    assert ExtractFailure(url="u", error="status: failed", status_code=404).kind == "target"
