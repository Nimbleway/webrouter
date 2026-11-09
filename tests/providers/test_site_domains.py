"""A domain filter that becomes a ``site:`` operator is refused before the call when it is not a host name."""

from __future__ import annotations

import contextlib

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.registry import get_provider_class
from web_router.types import SearchRequest

SITE_BACKENDS = ("brave", "deepseek", "webiq", "you-mcp")
"""The backends that write a domain filter into the query as ``site:``."""


@pytest.mark.parametrize("name", SITE_BACKENDS)
@pytest.mark.parametrize(("domains", "sends"), [(["https://example.com"], False), (["example.com"], True)])
def test_a_domain_filter_that_is_not_a_host_name_is_refused_unsent(
    monkeypatch: pytest.MonkeyPatch, name: str, domains: list[str], sends: bool
) -> None:
    for env in ("BRAVE_SEARCH_API_KEY", "DEEPSEEK_API_KEY", "WEBIQ_API_KEY"):
        monkeypatch.setenv(env, "test-key")
    sent: list[httpx.Request] = []
    provider = get_provider_class(name)(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)))
    request = SearchRequest(query="hi", include_domains=domains)
    if sends:
        with contextlib.suppress(ProviderError):  # the empty answer may not parse; only the send matters here
            provider.search(request)
    else:
        with pytest.raises(UnsupportedParameterError, match="include_domains") as raised:
            provider.search(request)
        assert "host name" in str(raised.value)
    assert len(sent) == int(sends)
