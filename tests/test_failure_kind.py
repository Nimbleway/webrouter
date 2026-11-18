"""Which failures say a backend refused one vendor mode's request: ``bad_request`` with a 400 or 422, credit left."""

from __future__ import annotations

import httpx
import pytest

from web_router.exceptions import ConfigurationError, ProviderError, ProviderNotInstalledError
from web_router.routing.failure_kind import mode_refused
from web_router.routing.record import error_code


def _http(status: int, text: str = "invalid type for this account", kind: str | None = None) -> ProviderError:
    """Exa's error for an HTTP ``status`` answered with ``text``, as the provider raises it."""
    sent = httpx.Request("POST", "https://api.exa.ai/search")
    answered = httpx.Response(status, request=sent, text=text)
    original = httpx.HTTPStatusError(text, request=sent, response=answered)
    return ProviderError("exa", f"HTTP {status}: {text}", original=original, kind=kind)


def _timeout() -> ProviderError:
    return ProviderError("exa", "timed out", original=httpx.ReadTimeout("read timed out"))


@pytest.mark.parametrize("status", [400, 422])
def test_a_400_or_422_bad_request_is_a_refused_mode(status: int) -> None:
    exc = _http(status)
    assert (exc.kind, exc.status_code) == ("bad_request", status)
    assert mode_refused(exc)


@pytest.mark.parametrize(
    "exc",
    [
        _http(405),
        ProviderError("exa", "connection reset"),
        _timeout(),
        ConfigurationError("no key"),
        ProviderNotInstalledError("exa", "search"),
    ],
    ids=["405", "no-status", "timeout", "configuration", "not-installed"],
)
def test_anything_else_is_not_a_refused_mode(exc: Exception) -> None:
    assert not mode_refused(exc)


@pytest.mark.parametrize("marker", ["INSUFFICIENT_FUNDS", "insufficient_quota", "You exceeded your current quota"])
def test_a_400_that_says_the_credit_ran_out_is_not_a_refused_mode(marker: str) -> None:
    assert not mode_refused(_http(400, f"error: {marker}"))


def test_a_422_whose_kind_is_auth_is_not_a_refused_mode() -> None:
    assert not mode_refused(_http(422, kind="auth"))


def test_a_hop_reads_the_status_the_backend_answered() -> None:
    assert error_code(_http(422)) == "422"
