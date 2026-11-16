"""What a provider name that cannot serve the call is told: unknown, or real but for another method."""

from __future__ import annotations

import pytest

from web_router import Provider, WebRouter
from web_router.constants import MCP_MSG_UNKNOWN_HANDLE
from web_router.exceptions import ProviderNotFoundError


def test_a_provider_asked_for_a_method_it_does_not_serve_says_so() -> None:
    """Brave is a real provider, so asking it to extract is not reported as an unknown name."""
    with pytest.raises(ProviderNotFoundError, match="'brave' does not serve this method") as caught:
        WebRouter().extract("https://example.com", provider=Provider.BRAVE)
    assert "jina" in str(caught.value)
    assert "brave" not in caught.value.available


def test_a_name_nothing_registers_is_still_unknown() -> None:
    with pytest.raises(ProviderNotFoundError, match="Unknown provider 'nope'"):
        WebRouter().search("x", provider="nope")


def test_the_unknown_handle_message_names_the_tools_the_server_has() -> None:
    message = MCP_MSG_UNKNOWN_HANDLE.format(handle="x", limit=1)
    assert "live_data_sources" in message
    assert "template" not in message
