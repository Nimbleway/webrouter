"""Over HTTP a model's browser runs on the server, so it may not open the server's own loopback or network.

Each case has the stdio control: with the switch at its default, the same addresses still open.
"""

from __future__ import annotations

import pytest

from web_router.constants_mcp_env import ENV_MCP_PRIVATE_URLS, MCP_PRIVATE_URLS_ON
from web_router.exceptions import ConfigurationError
from web_router.mcp.http_transport import http_env_defaults
from web_router.mcp.navigation import NavigationPolicy
from web_router.mcp.navigation_rules import private_refusal

CDP = "http://127.0.0.1:9222"
PRIVATE = [
    "http://127.0.0.1:8080/admin",
    "http://localhost:3000/",
    "http://anything.localhost:8080/",  # Chromium answers *.localhost with loopback, without DNS
    "http://[::1]/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://172.16.0.9:5432/",
    "http://[fd00::1]/",
]
PUBLIC = "https://example.com/"


@pytest.mark.parametrize("url", PRIVATE)
def test_over_http_a_private_address_is_refused(http_switches: None, url: str) -> None:
    reason = NavigationPolicy.for_session(CDP).refusal(url)

    assert reason is not None
    assert ENV_MCP_PRIVATE_URLS in reason


@pytest.mark.parametrize("url", PRIVATE)
def test_under_stdio_a_private_address_still_opens(stdio_switches: None, url: str) -> None:
    assert NavigationPolicy.for_session(CDP).refusal(url) is None


def test_over_http_a_name_that_resolves_privately_is_refused(http_switches: None) -> None:
    policy = NavigationPolicy.for_session(CDP)

    assert policy.refusal("http://intranet.example/") is None  # a name is judged by what it resolves to
    assert policy.resolved_refusal("http://intranet.example/", ["10.0.0.5"]) is not None
    assert policy.resolved_refusal(PUBLIC, ["93.184.216.34"]) is None


def test_over_http_a_public_address_still_opens(http_switches: None) -> None:
    assert NavigationPolicy.for_session(CDP).refusal(PUBLIC) is None
    assert NavigationPolicy.for_session(CDP).refusal("http://localhost.example.com/") is None


@pytest.mark.parametrize("host", ["x.localhost", "A.LOCALHOST", "localhost.", "x.localhost."])
def test_over_http_a_localhost_name_is_refused_before_any_lookup(http_switches: None, host: str) -> None:
    """Chromium answers every *.localhost name with loopback itself, so no DNS answer is needed to refuse it."""
    assert private_refusal(host) is not None


def test_the_operator_can_keep_private_addresses_on_over_http(
    monkeypatch: pytest.MonkeyPatch, stdio_switches: None
) -> None:
    monkeypatch.setenv(ENV_MCP_PRIVATE_URLS, MCP_PRIVATE_URLS_ON)
    http_env_defaults()

    assert NavigationPolicy.for_session(CDP).refusal(PRIVATE[0]) is None


def test_a_value_the_switch_does_not_take_stops_the_session(
    monkeypatch: pytest.MonkeyPatch, stdio_switches: None
) -> None:
    monkeypatch.setenv(ENV_MCP_PRIVATE_URLS, "sometimes")

    with pytest.raises(ConfigurationError, match=ENV_MCP_PRIVATE_URLS):
        NavigationPolicy.for_session(CDP)
