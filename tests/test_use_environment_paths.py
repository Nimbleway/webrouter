"""``use_environment=False`` beyond ``auto``'s chains: every other place a router reads a backend's key or setting."""

from __future__ import annotations

import pytest

from tests.use_environment_helpers import MINE, every_key_set, no_key_set
from web_router import ConfigurationError, NoAnswerProviderError, WebRouter
from web_router._environment import environment
from web_router.constants import (
    ENV_PERPLEXITY_API_KEY,
    PROVIDER_EXA,
    PROVIDER_JINA,
    PROVIDER_PERPLEXITY,
)
from web_router.constants_remote import ENV_REMOTE_ROUTER_API_KEY
from web_router.live_data_backends import usable_backends
from web_router.routing.remote_config import bearer_key


def test_an_unnamed_answer_backend_is_never_the_environments(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a non-answer default, the one configured answer backend serves: off, the environment configures none."""
    no_key_set(monkeypatch)
    monkeypatch.setenv(ENV_PERPLEXITY_API_KEY, "key-from-the-environment")

    assert WebRouter(PROVIDER_JINA).get_answer_provider().name == PROVIDER_PERPLEXITY
    with pytest.raises(NoAnswerProviderError):
        WebRouter(PROVIDER_JINA, use_environment=False).get_answer_provider()


def test_auto_answers_from_no_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    every_key_set(monkeypatch)

    assert WebRouter().get_answer_provider().members
    assert WebRouter(use_environment=False).get_answer_provider().members == ()


def test_live_data_tries_no_backend_keyed_only_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    every_key_set(monkeypatch)

    assert usable_backends(WebRouter())
    assert usable_backends(WebRouter(use_environment=False)) == []


def test_a_benchmark_compares_no_backend_keyed_only_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    every_key_set(monkeypatch)
    off = WebRouter(provider_config=MINE, use_environment=False)

    assert PROVIDER_PERPLEXITY in WebRouter()._default_benchmark_providers()
    assert PROVIDER_PERPLEXITY not in off._default_benchmark_providers()
    assert PROVIDER_EXA in off._default_benchmark_providers()
    assert PROVIDER_EXA in off._default_extract_benchmark_providers()
    assert len(off._default_extract_benchmark_providers()) < len(WebRouter()._default_extract_benchmark_providers())


def test_a_backends_setting_is_never_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """AgentCore's gateway, a LiteLLM proxy's URL and Merchant's profile: settings, read as keys are."""
    every_key_set(monkeypatch)
    off = WebRouter(provider_config={"exa-litellm": {"api_key": "mine"}}, use_environment=False)

    assert off.get_provider("agentcore")._gateway_url == ""  # type: ignore[attr-defined]
    assert off.get_provider("exa-litellm")._proxy_url == ""  # type: ignore[attr-defined]
    assert off.get_live_data_provider("merchant").agent_profile_url is None  # type: ignore[attr-defined]
    assert WebRouter().get_live_data_provider("merchant").agent_profile_url  # type: ignore[attr-defined]


def test_a_router_named_off_reads_no_key_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An LLM router reads its key as it is built; off, the variable it names counts as unset."""
    monkeypatch.setenv("OPENAI_API_KEY", "key-from-the-environment")
    config = {"preset": "openai", "model": "m"}

    WebRouter(router="llm", router_config=config).close()
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY is not set"):
        WebRouter(router="llm", router_config=config, use_environment=False)


def test_a_remote_routers_bearer_key_is_never_the_environments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_REMOTE_ROUTER_API_KEY, "key-from-the-environment")

    assert bearer_key(None) == "key-from-the-environment"
    with environment(False):
        assert bearer_key(None) is None
        assert bearer_key("mine") == "mine"


def test_an_error_off_says_the_environment_was_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting the variable it names would change nothing, so the error says so instead of a ``.env`` hint."""
    every_key_set(monkeypatch)

    with pytest.raises(ConfigurationError) as raised:
        WebRouter(use_environment=False).get_provider(PROVIDER_PERPLEXITY)

    assert str(raised.value).count("use_environment=False") == 1
    assert "A .env exists" not in str(raised.value)
    with environment(False):
        assert "use_environment" not in str(ConfigurationError("no variable named here"))


def test_a_setting_found_missing_when_called_says_the_environment_was_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """AgentCore and a LiteLLM proxy check their URL on the call, after the router's switch is gone."""
    every_key_set(monkeypatch)
    off = WebRouter(provider_config={"exa-litellm": {"api_key": "mine"}}, use_environment=False)

    for name in ("agentcore", "exa-litellm"):
        with pytest.raises(ConfigurationError, match="use_environment=False"):
            off.search("q", provider=name)
