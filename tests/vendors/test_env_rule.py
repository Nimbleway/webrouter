"""A project's .env and a vendor's variables: all of a credential or none, and never beside the user's own keys."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from web_router.mcp.env_credentials import apply_credentials

_FILE = Path("project/.env")
_GOOGLE_ADS = {
    "GOOGLE_ADS_DEVELOPER_TOKEN": "dev",
    "GOOGLE_ADS_CLIENT_ID": "id",
    "GOOGLE_ADS_CLIENT_SECRET": "secret",
    "GOOGLE_ADS_REFRESH_TOKEN": "refresh",
}


@pytest.fixture(autouse=True)
def _restore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every variable a test may load is put back afterwards, set or not."""
    datadog = ("DD_API_KEY", "DATADOG_API_KEY", "DD_APPLICATION_KEY", "DD_APP_KEY", "DATADOG_APP_KEY", "DD_SITE")
    for name in (*datadog, *_GOOGLE_ADS, "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_ZONE"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def test_a_project_setting_never_redirects_the_users_own_vendor_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_API_KEY", "user-key")
    monkeypatch.setenv("DD_APPLICATION_KEY", "user-app")
    loaded = apply_credentials(_FILE, {"DD_SITE": "datadoghq.eu"})
    assert loaded.loaded == ()
    assert loaded.already_set == ("DD_SITE",)
    assert "DD_SITE" not in os.environ


@pytest.mark.parametrize("name", ["GOOGLE_ADS_LOGIN_CUSTOMER_ID", "GOOGLE_ADS_CLIENT_SECRET"])
def test_nothing_of_a_vendor_loads_beside_the_users_own_credentials(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    for variable, value in _GOOGLE_ADS.items():
        if variable != name:
            monkeypatch.setenv(variable, value)
    assert apply_credentials(_FILE, {name: "project-value"}).loaded == ()


def test_a_lone_vendor_credential_loads_nothing() -> None:
    assert apply_credentials(_FILE, {"DD_API_KEY": "project-key"}).loaded == ()
    assert "DD_API_KEY" not in os.environ


def test_a_whole_vendor_credential_set_loads_when_the_user_has_none() -> None:
    values = {"DATADOG_API_KEY": "k", "DD_APP_KEY": "a", "DD_SITE": "datadoghq.eu"}
    assert set(apply_credentials(_FILE, values).loaded) == set(values)
    assert os.environ["DD_SITE"] == "datadoghq.eu"


def test_a_file_of_only_a_vendors_settings_loads_them() -> None:
    assert apply_credentials(_FILE, {"DD_SITE": "datadoghq.eu"}).loaded == ("DD_SITE",)


def test_a_backends_partner_rule_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "user-token")
    assert apply_credentials(_FILE, {"BRIGHTDATA_ZONE": "project-zone"}).loaded == ()
