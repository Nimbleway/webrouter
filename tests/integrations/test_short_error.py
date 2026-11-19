"""short_error, the one-line failure for a model, a CLI or a log; and the rendering every agent tool shares."""

from __future__ import annotations

import json

import pytest

from web_router.constants_extract import MSG_EXTRACT_NEAR_EMPTY
from web_router.exceptions import ConfigurationError, DeadlineExceededError, ProviderError
from web_router.exceptions_typed import AllProvidersFailedError
from web_router.integrations import short_error
from web_router.integrations._render import render_extract
from web_router.types import ExtractedDocument, ExtractResponse


def _throttled() -> ProviderError:
    error = ProviderError("tavily", "HTTP 402: pay via x402 at https://vendor.example/pay", kind="rate_limit")
    error.status_code, error.retry_after = 402, 30.0
    return error


def test_a_backend_failure_says_who_why_and_when_to_retry_and_leaves_the_vendor_text_out() -> None:
    assert short_error(_throttled()) == (
        "tavily: the backend is throttling this key or it is out of quota "
        "(kind=rate_limit, retryable=true, status=402, retry_after=30s)."
    )


def test_an_auto_failure_names_each_backend_it_tried_and_its_kind() -> None:
    dead = ProviderError("nimble", "Could not resolve host: nope.invalid")
    error = AllProvidersFailedError("auto", "every backend failed", {"nimble": dead, "tavily": _throttled()})

    assert short_error(error) == (
        "auto: the backend failed (kind=upstream, retryable=true, retry_after=30s). "
        "Tried: nimble (target), tavily (rate_limit)."
    )


def test_a_call_out_of_time_is_a_retryable_timeout() -> None:
    assert short_error(DeadlineExceededError("exa", 20.0)) == "exa: no answer in time (kind=timeout, retryable=true)."


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConfigurationError("No key for exa.\nSet EXA_API_KEY."), "No key for exa."),
        (RuntimeError(""), "RuntimeError"),
    ],
)
def test_an_error_without_a_kind_is_its_own_first_line(error: Exception, expected: str) -> None:
    assert short_error(error) == expected


def test_the_line_is_cut_to_limit() -> None:
    assert short_error(_throttled(), limit=20) == "tavily: the backend ... [truncated]"


def test_the_model_reads_web_routers_own_page_warnings_not_a_vendors() -> None:
    warnings = ["This page maybe not yet fully loaded", MSG_EXTRACT_NEAR_EMPTY]
    document = ExtractedDocument(url="https://a.example", content="hi", warnings=warnings)

    shown = json.loads(render_extract(ExtractResponse(provider="p", documents=[document])))

    assert shown["documents"][0]["warnings"] == [MSG_EXTRACT_NEAR_EMPTY]
    assert "failures" not in shown
