"""The local network keeping a call from the backend is its own kind; a chain that reached no host names them."""

from __future__ import annotations

import io
import json
import socket
from collections.abc import Callable

import httpx
import pytest
import requests
import urllib3

from tests.test_auto_stops import _searcher, chain  # noqa: F401  (``chain`` is a fixture)
from web_router import AllProvidersFailedError
from web_router.cli.output import write_error
from web_router.exceptions import ProviderError
from web_router.providers.auto import AutoProvider
from web_router.types import ExtractFailure, SearchRequest
from web_router.types_errors import CHAIN_STOPPING_KINDS


def _request(host: str) -> httpx.Request:
    return httpx.Request("POST", f"https://{host}/search")


def _proxy_refused(host: str = "api.tavily.com") -> httpx.ProxyError:
    return httpx.ProxyError("403 Forbidden", request=_request(host))


def _not_resolved(host: str = "mcp.exa.ai") -> httpx.ConnectError:
    error = httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known", request=_request(host))
    error.__cause__ = socket.gaierror(8, "nodename nor servname provided, or not known")
    return error


def _refused(host: str = "api.keenable.ai") -> httpx.ConnectError:
    error = httpx.ConnectError("[Errno 61] Connection refused", request=_request(host))
    error.__cause__ = ConnectionRefusedError(61, "Connection refused")
    return error


def _requests_proxy_refused() -> requests.exceptions.ProxyError:
    """As ``requests`` reports a CONNECT its proxy answered 403, three errors deep."""
    tunnel = urllib3.exceptions.ProxyError("Unable to connect to proxy", OSError("Tunnel connection failed: 403"))
    prepared = requests.Request("POST", "https://api.tavily.com/search").prepare()
    error = requests.exceptions.ProxyError(tunnel, request=prepared)
    error.__cause__ = tunnel
    return error


class APIConnectionError(Exception):
    """Stands in for an SDK's "Connection error.", which carries httpx's error as its cause."""


class ClientProxyConnectionError(Exception):
    """Stands in for aiohttp's proxy connection failure without importing its optional transport."""


def _sdk_wrapped() -> APIConnectionError:
    error = APIConnectionError("Connection error.")
    error.__cause__ = _proxy_refused("sdk.nimbleway.com")
    return error


@pytest.mark.parametrize(
    ("original", "kind"),
    [
        (_proxy_refused(), "network_blocked"),
        (_requests_proxy_refused(), "network_blocked"),
        (_sdk_wrapped(), "network_blocked"),
        (ClientProxyConnectionError("proxy refused"), "network_blocked"),
        (_not_resolved(), "unreachable"),
        (_refused(), "unreachable"),
    ],
)
def test_the_local_network_refusing_the_backend_is_its_own_kind(original: BaseException, kind: str) -> None:
    error = ProviderError("acme", "search failed", original=original)

    assert error.kind == kind
    assert error.retryable is False, "the same network refuses the same host again"
    assert kind not in CHAIN_STOPPING_KINDS, "another backend's host may be allowed"


def test_a_proxy_asking_for_credentials_is_network_blocked() -> None:
    request = _request("api.exa.ai")
    original = httpx.HTTPStatusError("407", request=request, response=httpx.Response(407, request=request))

    assert ProviderError("exa", "HTTP 407", original=original).kind == "network_blocked"


def test_a_connection_dropped_without_a_network_cause_stays_upstream() -> None:
    assert ProviderError("acme", "failed", original=httpx.ConnectError("Connection reset")).kind == "upstream"


def test_every_backend_refused_names_each_host_once() -> None:
    failures = {
        "tavily": ProviderError("tavily", "failed", original=_proxy_refused()),
        "tavily-again": ProviderError("tavily", "failed", original=_proxy_refused()),
        "exa-mcp": ProviderError("exa-mcp", "failed", original=_not_resolved()),
        "keenable": ProviderError("keenable", "failed", kind="unreachable"),
    }

    error = AllProvidersFailedError("auto", "every backend failed: ...", failures)

    assert error.blocked_hosts == ["api.tavily.com", "mcp.exa.ai"], "only hosts an error named"
    assert error.kind == "network_blocked" and error.retryable is False
    reachable = "[auto] no backend host is reachable from this machine: api.tavily.com, mcp.exa.ai, keenable's host."
    assert str(error).startswith(reachable)


def test_one_backend_failing_upstream_keeps_the_usual_error() -> None:
    failures = {
        "tavily": ProviderError("tavily", "failed", original=_proxy_refused()),
        "brave": ProviderError("brave", "HTTP 503", kind="upstream"),
    }

    error = AllProvidersFailedError("auto", "every backend failed: ...", failures)

    assert error.blocked_hosts == [] and error.kind == "upstream"
    assert "every backend failed" in str(error)


def test_auto_tries_the_next_backend_after_a_blocked_host(chain: Callable[..., AutoProvider]) -> None:  # noqa: F811
    blocked, spare = _searcher("blocked", _proxy_refused), _searcher("spare")

    assert chain(blocked, spare).search(SearchRequest(query="q")).provider == "spare"
    assert spare.calls == 1


def test_auto_reaching_no_host_raises_the_hosts(chain: Callable[..., AutoProvider]) -> None:  # noqa: F811
    first, second = _searcher("first", _proxy_refused), _searcher("second", _not_resolved)

    with pytest.raises(AllProvidersFailedError) as caught:
        chain(first, second).search(SearchRequest(query="q"))

    assert caught.value.blocked_hosts == ["api.tavily.com", "mcp.exa.ai"]


def test_the_cli_prints_the_hosts_and_each_hops_kind() -> None:
    failures = {
        "tavily": ProviderError("tavily", "failed", original=_proxy_refused()),
        "exa-mcp": ProviderError("exa-mcp", "failed", original=_not_resolved()),
    }
    err = io.StringIO()

    write_error(AllProvidersFailedError("auto", "every backend failed", failures), err)

    envelope = json.loads(err.getvalue())["error"]
    assert envelope["kind"] == "network_blocked"
    assert envelope["hops"] == {"tavily": "network_blocked", "exa-mcp": "unreachable"}
    assert envelope["blocked_hosts"] == ["api.tavily.com", "mcp.exa.ai"]
    assert "\n" not in envelope["message"]


def test_urls_failing_on_both_network_kinds_are_not_retryable() -> None:
    failures = [
        ExtractFailure(url=f"https://{kind}.test", error="x", kind=kind) for kind in ("network_blocked", "unreachable")
    ]

    assert ExtractFailure.batch_kind(failures) == "network_blocked"


class ProxyErrorSettings(Exception):
    """A provider's own error that only mentions a proxy in its name."""


def test_an_error_merely_named_for_a_proxy_is_not_a_refusal() -> None:
    assert ProviderError("acme", "bad config", original=ProxyErrorSettings("bad")).kind != "network_blocked"
