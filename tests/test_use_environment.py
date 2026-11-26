"""``use_environment=False``: a router whose backends take keys from ``provider_config`` only, never ``os.environ``.

One process may serve several users -- a gateway launched with one user's
``.env`` -- so a router built for another must not bill that user's keys.
"""

from __future__ import annotations

from typing import Any

import pytest
from nimble_python import Omit

from tests.use_environment_helpers import ENV_KEY, MINE, every_key_set, no_key_set
from web_router import AsyncWebRouter, ConfigurationError, WebRouter
from web_router.constants import (
    PROVIDER_BROWSER_USE,
    PROVIDER_EXA,
    PROVIDER_NIMBLE,
    PROVIDER_PERPLEXITY,
    PROVIDER_STEEL,
    PROVIDER_TAVILY,
)
from web_router.registry import configured_providers

_ROUTERS = [WebRouter, AsyncWebRouter]


def _chains(router: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return router.get_provider().members, router.get_extract_provider().members


@pytest.mark.parametrize("router_class", _ROUTERS)
def test_off_it_chains_what_an_empty_environment_would(monkeypatch: pytest.MonkeyPatch, router_class: Any) -> None:
    """The backends keyed in ``provider_config``, then the keyless tail: none of the environment's."""
    no_key_set(monkeypatch)
    expected = _chains(router_class(provider_config=MINE))
    every_key_set(monkeypatch)

    chains = _chains(router_class(provider_config=MINE, use_environment=False))

    assert chains == expected
    assert PROVIDER_EXA in chains[0]
    assert PROVIDER_PERPLEXITY not in chains[0]


@pytest.mark.parametrize("router_class", _ROUTERS)
def test_on_by_default_it_chains_the_environment_keys(monkeypatch: pytest.MonkeyPatch, router_class: Any) -> None:
    every_key_set(monkeypatch)

    on, off = router_class(provider_config=MINE), router_class(provider_config=MINE, use_environment=False)

    assert all(len(full) > len(own) for full, own in zip(_chains(on), _chains(off), strict=True))


def test_no_member_built_for_a_call_carries_an_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A keyless member stays keyless, and only the backend keyed in code holds a credential."""
    every_key_set(monkeypatch)
    router = WebRouter(provider_config=MINE, use_environment=False)

    for auto in (router.get_provider(), router.get_extract_provider()):
        for position, name in enumerate(auto.members):
            member = auto._member(position)
            assert member.api_key != ENV_KEY, name
            assert member.holds_credential == (name == PROVIDER_EXA), name


def test_the_keyless_sdk_clients_never_pick_up_the_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tavily's and Nimble's SDKs read their variable for a key of ``None``, so they are never handed one."""
    every_key_set(monkeypatch)
    auto = WebRouter(use_environment=False).get_provider()
    members = {name: auto._member(position) for position, name in enumerate(auto.members)}

    assert members[PROVIDER_TAVILY]._client.api_key is None
    assert members[PROVIDER_NIMBLE]._async_client.api_key == ""


def test_a_nimble_key_in_code_never_goes_to_the_environments_host_or_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Nimble SDK reads its host and extra headers from the environment, so they are given it explicitly."""
    every_key_set(monkeypatch)
    monkeypatch.setenv("NIMBLE_BASE_URL", "https://launch-user.example.com")
    monkeypatch.setenv("NIMBLE_CUSTOM_HEADERS", "X-Launch-User: theirs\nX-Mine: launch")
    config = {PROVIDER_NIMBLE: {"api_key": "mine", "default_headers": {"X-Mine": "mine"}}}

    keyed = WebRouter(provider_config=config, use_environment=False).get_provider(PROVIDER_NIMBLE)
    keyless = WebRouter(use_environment=False).get_extract_provider()._member(0)  # auto's first: keyless Nimble
    for client in (keyed._async_client, keyed._client, keyless._async_client):
        sent = {name: value for name, value in client.default_headers.items() if not isinstance(value, Omit)}
        assert client.base_url.host == "sdk.nimbleway.com"
        assert "X-Launch-User" not in sent
    assert keyed._async_client.default_headers["X-Mine"] == "mine"
    assert keyless.name == PROVIDER_NIMBLE
    assert (
        WebRouter(provider_config=config).get_provider(PROVIDER_NIMBLE)._async_client.base_url.host
        != "sdk.nimbleway.com"
    )


@pytest.mark.parametrize(
    ("getter", "name"),
    [
        ("get_provider", PROVIDER_PERPLEXITY),
        ("get_extract_provider", PROVIDER_TAVILY),
        ("get_research_provider", PROVIDER_PERPLEXITY),
        ("get_answer_provider", PROVIDER_PERPLEXITY),
        ("get_browser_provider", PROVIDER_STEEL),
        ("get_browse_provider", PROVIDER_BROWSER_USE),
    ],
)
def test_a_backend_named_off_raises_rather_than_take_the_environment_key(
    monkeypatch: pytest.MonkeyPatch, getter: str, name: str
) -> None:
    every_key_set(monkeypatch)

    with pytest.raises(ConfigurationError, match="use_environment=False"):
        getattr(WebRouter(use_environment=False), getter)(name)
    assert getattr(WebRouter(), getter)(name).api_key == ENV_KEY


def test_the_switch_ends_with_the_router_that_set_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A context variable, never a global: a router built after, or code outside one, reads the environment."""
    every_key_set(monkeypatch)
    assert PROVIDER_PERPLEXITY not in WebRouter(use_environment=False).get_provider().members

    assert PROVIDER_PERPLEXITY in configured_providers()
    assert PROVIDER_PERPLEXITY in WebRouter().get_provider().members


def test_a_key_in_code_still_reaches_a_backend_named_off(monkeypatch: pytest.MonkeyPatch) -> None:
    every_key_set(monkeypatch)
    config = {PROVIDER_PERPLEXITY: {"api_key": "mine"}}

    provider = WebRouter(provider_config=config, use_environment=False).get_provider(PROVIDER_PERPLEXITY)

    assert provider.api_key == "mine"
