"""Shared key and setting resolution."""

from __future__ import annotations

import pytest

from web_router.exceptions import ConfigurationError
from web_router.providers._utils import resolve_api_key, resolve_setting

ENV_VAR = "ACME_TEST_VALUE"


@pytest.mark.parametrize("value", ["", " ", "\n"])
def test_a_blank_key_variable_is_missing(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """A whitespace-only key would otherwise reach httpx as an illegal header and fail every call."""
    monkeypatch.setenv(ENV_VAR, value)
    with pytest.raises(ConfigurationError):
        resolve_api_key(None, env_var=ENV_VAR, provider="acme")


@pytest.mark.parametrize("value", ["", " ", "\n"])
def test_a_blank_setting_variable_is_missing(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ENV_VAR, value)
    with pytest.raises(ConfigurationError):
        resolve_setting(None, env_var=ENV_VAR, provider="acme", setting="zone")


def test_a_padded_value_comes_back_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "  abc\n")
    assert resolve_api_key(None, env_var=ENV_VAR, provider="acme") == "abc"
    assert resolve_setting(None, env_var=ENV_VAR, provider="acme", setting="zone") == "abc"


@pytest.mark.parametrize("explicit", ["", " "])
def test_a_blank_explicit_value_falls_back_to_the_variable(monkeypatch: pytest.MonkeyPatch, explicit: str) -> None:
    monkeypatch.setenv(ENV_VAR, "abc")
    assert resolve_api_key(explicit, env_var=ENV_VAR, provider="acme") == "abc"
    assert resolve_setting(explicit, env_var=ENV_VAR, provider="acme", setting="zone") == "abc"
