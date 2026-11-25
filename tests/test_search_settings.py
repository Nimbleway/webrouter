"""A search backend that needs a setting beside its credential is configured only with both, and says so."""

from __future__ import annotations

import pytest

from web_router.mcp import methods
from web_router.registry import configured_providers

BACKENDS = [
    ("oxylabs", "OXYLABS_PASSWORD", "OXYLABS_USERNAME"),
    ("brightdata", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_ZONE"),
]


@pytest.mark.parametrize(("name", "credential", "setting"), BACKENDS)
def test_a_credential_without_its_setting_configures_no_search(
    monkeypatch: pytest.MonkeyPatch, name: str, credential: str, setting: str
) -> None:
    monkeypatch.setenv(credential, "test-key")
    monkeypatch.delenv(setting, raising=False)
    assert name not in configured_providers()

    monkeypatch.setenv(setting, "test-setting")
    assert name in configured_providers()


@pytest.mark.parametrize(("name", "credential", "setting"), BACKENDS)
def test_the_unlock_hint_names_the_missing_setting(
    monkeypatch: pytest.MonkeyPatch, name: str, credential: str, setting: str
) -> None:
    monkeypatch.setenv(credential, "test-key")
    monkeypatch.delenv(setting, raising=False)

    assert methods.unlock()["search"][name] == [setting]
