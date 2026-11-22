"""You.com over MCP: a domain filter with a path is refused, since You.com matches either list on the host alone."""

from __future__ import annotations

import httpx
import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.providers.you_mcp import YouMcpProvider
from web_router.types import SearchRequest


@pytest.mark.parametrize(
    ("field", "domains"),
    [
        ("include_domains", ["github.com/python"]),
        ("include_domains", ["a.com", "docs.b.org/guide/"]),
        ("exclude_domains", ["github.com/python"]),
    ],
)
def test_a_domain_filter_with_a_path_is_refused_before_anything_is_sent(
    transport: httpx.MockTransport, sent: list[httpx.Request], field: str, domains: list[str]
) -> None:
    """You.com matches site: and exclude_domains on the host alone (checked live), so a path would be dropped.

    An include with a path served pages outside it; an exclude with a path dropped the whole host.
    """
    provider = YouMcpProvider(transport=transport)

    with pytest.raises(UnsupportedParameterError, match="no path") as caught:
        provider.search(SearchRequest(query="hi", **{field: domains}))

    assert caught.value.values == {field: domains}
    assert not sent


@pytest.mark.parametrize("field", ["include_domains", "exclude_domains"])
def test_auto_passes_over_you_mcp_for_a_domain_with_a_path(field: str) -> None:
    assert YouMcpProvider.refused_values(SearchRequest(query="hi", **{field: ["github.com/python"]})) == {
        field: ["github.com/python"]
    }


def test_a_bare_host_with_a_trailing_slash_is_still_served() -> None:
    """A trailing slash names no path, so it is the host it follows."""
    assert YouMcpProvider.refused_values(SearchRequest(query="hi", include_domains=["github.com/"])) == {}


def test_an_exclude_given_as_a_url_with_a_path_is_refused_too() -> None:
    """exclude_domains is a tool argument, so a bare-host URL goes as given; one with a path would still lose it."""
    assert YouMcpProvider.refused_values(SearchRequest(query="hi", exclude_domains=["https://example.com/"])) == {}
    assert YouMcpProvider.refused_values(SearchRequest(query="hi", exclude_domains=["https://example.com/a"])) == {
        "exclude_domains": ["https://example.com/a"]
    }
