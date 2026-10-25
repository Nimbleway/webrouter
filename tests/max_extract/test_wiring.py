"""Where ``max`` sits in the package: the registry, the router's config, and the live keyless race."""

from __future__ import annotations

from typing import Any

import pytest

from tests.max_extract.conftest import URL, _member, _Recorder
from web_router import WebRouter
from web_router.constants import BENCHMARK_EXCLUDED, PROVIDER_MAX
from web_router.exceptions import ConfigurationError
from web_router.providers.auto import AutoExtractProvider
from web_router.providers.max import MaxExtractProvider
from web_router.registry import available_extract_providers, get_extract_provider_class, register_extract_provider


def test_max_is_an_extract_backend_and_nothing_else() -> None:
    assert PROVIDER_MAX in available_extract_providers()
    assert get_extract_provider_class(PROVIDER_MAX) is MaxExtractProvider
    assert PROVIDER_MAX in BENCHMARK_EXCLUDED, "a row for max would repeat one of the others"


def test_max_races_exactly_the_members_auto_would_try(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBLE_API_KEY", "x")

    assert MaxExtractProvider().members == AutoExtractProvider().members


def test_the_router_hands_max_the_members_config_as_it_does_auto(members: _Recorder) -> None:
    seen: dict[str, Any] = {}

    class Member(_member(members, "configured")):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)
            super().__init__(**kwargs)

    register_extract_provider("configured", Member)
    router = WebRouter(
        PROVIDER_MAX,
        provider_config={"configured": {"api_key": "k-123"}, PROVIDER_MAX: {"chain": ["configured"], "hedge": 0.2}},
    )
    try:
        response = router.extract(URL)
    finally:
        router.close()

    assert response.provider == "configured"
    assert seen["api_key"] == "k-123"
    assert router.get_extract_provider().hedge == 0.2  # type: ignore[attr-defined]


def test_a_chain_set_under_auto_narrows_max_too(members: _Recorder) -> None:
    """Someone who took a paid backend out of auto's chain must not keep paying for it under max."""
    _member(members, "kept")

    router = WebRouter(PROVIDER_MAX, provider_config={"auto": {"chain": {"extract": ["kept"]}}})
    try:
        assert router.get_extract_provider().members == ("kept",)  # type: ignore[attr-defined]
        assert router.get_extract_provider("auto").members == ("kept",)  # type: ignore[attr-defined]
    finally:
        router.close()


def test_a_chain_set_under_max_wins_over_the_one_under_auto(members: _Recorder) -> None:
    _member(members, "for-auto")
    _member(members, "for-max")

    router = WebRouter(
        PROVIDER_MAX,
        provider_config={"auto": {"chain": ["for-auto"]}, PROVIDER_MAX: {"chain": {"extract": ["for-max"]}}},
    )
    try:
        assert router.get_extract_provider().members == ("for-max",)  # type: ignore[attr-defined]
        assert router.get_extract_provider("auto").members == ("for-auto",)  # type: ignore[attr-defined]
    finally:
        router.close()


def test_a_members_config_set_under_auto_reaches_max_s_members(members: _Recorder) -> None:
    seen: dict[str, Any] = {}

    class Member(_member(members, "configured")):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)
            super().__init__(**kwargs)

    register_extract_provider("configured", Member)
    router = WebRouter(
        PROVIDER_MAX,
        provider_config={"auto": {"chain": ["configured"], "members_config": {"configured": {"api_key": "k-456"}}}},
    )
    try:
        assert router.extract(URL).provider == "configured"
    finally:
        router.close()

    assert seen["api_key"] == "k-456"


def test_a_sync_router_refuses_an_async_client_in_max_s_chain() -> None:
    """Through max the call would quietly lose that member, as it would through auto; say so at construction instead."""
    with pytest.raises(ConfigurationError, match="nimble"):
        WebRouter(PROVIDER_MAX, provider_config={PROVIDER_MAX: {"chain": [("nimble", {"async_client": object()})]}})


@pytest.mark.integration
def test_live_max_races_the_keyless_tier_with_no_key_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Needs no credential: with none, the race is between the keyless backends."""
    from web_router.constants import EXTRACT_ENV_BY_PROVIDER

    for env_vars in EXTRACT_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)

    with WebRouter(PROVIDER_MAX) as router:
        members = router.get_extract_provider().members  # type: ignore[attr-defined]
        response = router.extract("https://example.com", format="markdown")

    assert response.provider in members and response.provider != PROVIDER_MAX
    assert response.documents[0].content
