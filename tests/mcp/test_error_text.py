"""A failed tool call's text ends in the error's kind and whether a retry may help, so a model need not guess."""

from __future__ import annotations

import pytest

from web_router.exceptions import ProviderError
from web_router.exceptions_typed import InvalidParameterError
from web_router.mcp.messages import error_text


@pytest.mark.parametrize(
    ("error", "tail"),
    [
        (ProviderError("nimble", "HTTP 400: nx_domain_error"), " (kind=target, retryable=false)"),
        (ProviderError("you", "MCP call failed", kind="rate_limit"), " (kind=rate_limit, retryable=true)"),
    ],
)
def test_a_backend_failure_ends_in_its_kind(error: ProviderError, tail: str) -> None:
    assert error_text(error) == str(error) + tail


def test_an_error_without_a_kind_has_no_tail() -> None:
    error = InvalidParameterError("query", "Text must hold something other than whitespace.")

    assert error_text(error) == str(error)
