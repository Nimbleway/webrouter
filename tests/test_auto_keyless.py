"""Where the keyless tier sits in ``auto``'s default chains once some of its members are held with a key."""

from __future__ import annotations

import pytest

from web_router.constants import (
    ENV_BY_PROVIDER,
    ENV_EXA_API_KEY,
    ENV_FIRECRAWL_API_KEY,
    ENV_KEENABLE_API_KEY,
    ENV_TAVILY_API_KEY,
    EXTRACT_ENV_BY_PROVIDER,
    PROVIDER_EXA,
    PROVIDER_EXA_MCP,
    PROVIDER_FIRECRAWL,
    PROVIDER_JINA,
    PROVIDER_KEENABLE,
    PROVIDER_NIMBLE,
    PROVIDER_PARALLEL_MCP,
    PROVIDER_TAVILY,
    PROVIDER_YOU_MCP,
)
from web_router.providers.auto import AutoExtractProvider, AutoProvider

_EVERY_SEARCH_KEY = [env_var for env_var in ENV_BY_PROVIDER.values() if env_var]
_EVERY_EXTRACT_KEY = sorted({env_var for env_vars in EXTRACT_ENV_BY_PROVIDER.values() for env_var in env_vars})


def test_a_keyed_extract_provider_is_preferred_over_an_anonymous_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in _EVERY_EXTRACT_KEY:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv(ENV_EXA_API_KEY, "test-key")

    chain = [name for name, _ in AutoExtractProvider.default_chain()]

    assert chain == [
        PROVIDER_NIMBLE,
        PROVIDER_TAVILY,
        PROVIDER_JINA,
        PROVIDER_FIRECRAWL,
        PROVIDER_KEENABLE,
        PROVIDER_EXA,
    ]


def test_a_backend_held_with_a_key_is_not_tried_again_as_keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    """A keyless member resolves the key when one is set, so a second try would repeat the same request."""
    for env_var in (*_EVERY_SEARCH_KEY, *_EVERY_EXTRACT_KEY):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv(ENV_TAVILY_API_KEY, "test-key")
    monkeypatch.setenv(ENV_FIRECRAWL_API_KEY, "test-key")

    search = [name for name, _ in AutoProvider.default_chain()]
    extract = [name for name, _ in AutoExtractProvider.default_chain()]

    assert search == [
        PROVIDER_TAVILY,
        PROVIDER_NIMBLE,
        PROVIDER_YOU_MCP,
        PROVIDER_EXA_MCP,
        PROVIDER_PARALLEL_MCP,
        PROVIDER_KEENABLE,
    ]
    assert extract == [PROVIDER_TAVILY, PROVIDER_FIRECRAWL, PROVIDER_NIMBLE, PROVIDER_JINA, PROVIDER_KEENABLE]


def test_a_backend_appended_with_a_key_leaves_the_keyless_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keenable joined after the keyless tier: held with a key it is tried there, once, never early as "keyless"."""
    for env_var in (*_EVERY_SEARCH_KEY, *_EVERY_EXTRACT_KEY):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv(ENV_KEENABLE_API_KEY, "test-key")

    search = [name for name, _ in AutoProvider.default_chain()]
    extract = [name for name, _ in AutoExtractProvider.default_chain()]

    assert search == [
        PROVIDER_NIMBLE,
        PROVIDER_TAVILY,
        PROVIDER_YOU_MCP,
        PROVIDER_EXA_MCP,
        PROVIDER_PARALLEL_MCP,
        PROVIDER_KEENABLE,
    ]
    assert extract == [PROVIDER_NIMBLE, PROVIDER_TAVILY, PROVIDER_JINA, PROVIDER_FIRECRAWL, PROVIDER_KEENABLE]
    assert dict(AutoProvider.default_chain())[PROVIDER_KEENABLE] == {}, "the keyed entry, not the keyless one"
