"""The OAuth token cache: how long a token is believed, and one exchange per token across sync and async."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from tests.vendors.conftest import CREDENTIALS, OAUTH
from web_router.exceptions import ProviderError
from web_router.vendors.schema import VendorOAuth2
from web_router.vendors.tokens import TokenCache, token_request

_OAUTH = VendorOAuth2.model_validate(OAUTH["auth"]["oauth2"])


class _TokenEndpoint:
    """A token endpoint answering ``body``, counting its calls, optionally slow."""

    def __init__(self, body: bytes, delay: float = 0.0) -> None:
        self.body, self.delay, self.calls = body, delay, 0
        self._guard = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self._guard:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return httpx.Response(200, content=self.body, headers={"content-type": "application/json"})


def _body(expires_in: str | None) -> bytes:
    lifetime = "" if expires_in is None else f', "expires_in": {expires_in}'
    return f'{{"access_token": "t"{lifetime}}}'.encode()


def _twice(endpoint: _TokenEndpoint) -> None:
    cache, request = TokenCache(), token_request("oauthco", _OAUTH, CREDENTIALS["oauthco"])
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        assert cache.get(request, client) == "t"
        assert cache.get(request, client) == "t"


@pytest.mark.parametrize("expires_in", ["0", "-5", "30"])
def test_a_lifetime_at_or_inside_the_refresh_margin_is_not_reused(expires_in: str) -> None:
    endpoint = _TokenEndpoint(_body(expires_in))
    _twice(endpoint)
    assert endpoint.calls == 2


@pytest.mark.parametrize("expires_in", ["3600", None])
def test_a_long_or_unstated_lifetime_is_reused(expires_in: str | None) -> None:
    endpoint = _TokenEndpoint(_body(expires_in))
    _twice(endpoint)
    assert endpoint.calls == 1


@pytest.mark.parametrize("expires_in", ["NaN", "Infinity"])
def test_a_non_finite_lifetime_is_an_unreadable_answer(expires_in: str) -> None:
    with pytest.raises(ProviderError, match="not finite"):
        _twice(_TokenEndpoint(_body(expires_in)))


def test_a_sync_and_an_async_call_on_a_cold_cache_make_one_exchange() -> None:
    endpoint = _TokenEndpoint(_body("3600"), delay=0.2)
    cache, request = TokenCache(), token_request("oauthco", _OAUTH, CREDENTIALS["oauthco"])
    got: list[str] = []

    def sync_side() -> None:
        with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
            got.append(cache.get(request, client))

    async def async_side() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
            got.append(await cache.aget(request, client))

    thread = threading.Thread(target=sync_side)
    thread.start()
    asyncio.run(async_side())
    thread.join()
    assert got == ["t", "t"]
    assert endpoint.calls == 1


@pytest.mark.parametrize(
    "change",
    [
        {"token_url": "https://{region}.example.com/tok"},
        {"scope": "read:{tenant}"},
    ],
)
def test_an_oauth_token_url_or_scope_holds_no_placeholder(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="cannot hold a placeholder"):
        VendorOAuth2.model_validate({**OAUTH["auth"]["oauth2"], **change})


def test_a_literal_scope_is_sent_as_written() -> None:
    oauth = VendorOAuth2.model_validate({**OAUTH["auth"]["oauth2"], "scope": "read write"})
    assert token_request("oauthco", oauth, CREDENTIALS["oauthco"]).form["scope"] == "read write"
