"""A stopped local backend remains retryable instead of suggesting a network allowlist."""

from __future__ import annotations

import httpx
import pytest

from tests.test_network_blocked import _not_resolved
from web_router import AllProvidersFailedError, ProviderError


@pytest.mark.parametrize(
    "host",
    [
        "localhost:4000",
        "LOCALHOST.:4000",
        "127.0.0.1",
        "[::1]",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.2",
        "169.254.1.2",
        "[fe80::1]",
        "[fd00::1]",
    ],
)
def test_a_refused_local_backend_stays_retryable(host: str) -> None:
    original = httpx.ConnectError(
        "All connection attempts failed", request=httpx.Request("POST", f"http://{host}/v1/search")
    )
    original.__cause__ = ConnectionRefusedError(61, "Connection refused")
    failure = ProviderError("litellm", "boom", original=original)

    assert failure.kind == "upstream"
    assert failure.retryable is True
    aggregate = AllProvidersFailedError("auto", "every backend failed", {"litellm": failure})
    assert aggregate.blocked_hosts == []
    assert "allow" not in str(aggregate)


def test_a_refusal_without_a_request_stays_retryable() -> None:
    failure = ProviderError("local", "cdp", original=ConnectionRefusedError(61, "Connection refused"))

    assert failure.kind == "upstream"
    assert failure.retryable is True


def test_dns_failure_remains_unreachable_even_for_a_local_name() -> None:
    failure = ProviderError("local", "failed", original=_not_resolved("localhost"))

    assert failure.kind == "unreachable"
    assert failure.retryable is False
