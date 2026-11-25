"""The data-driven router's provider table as read from the release: which rows and files it refuses, and how."""

from __future__ import annotations

import json
from typing import Any

import pytest

from web_router.constants_data_driven import DATA_DRIVEN_MANIFEST_FILE
from web_router.exceptions import ConfigurationError
from web_router.routing.data_driven import providers as providers_module
from web_router.routing.data_driven.providers import Point, ProviderTable, checked_points, shipped_providers

FULL = {"id": "search:a", "quality": 1, "ci95": 1, "usd_per_1k": 1, "p50_s": 1}
NOT_USABLE = "the data-driven router's provider table is not usable: "


@pytest.fixture
def release(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The files the router reads, to alter: a manifest naming a provider table of one good row."""
    named = {"provider_table": {"file": "provider_table.json", "sha256": None}}
    files = {"manifest.json": named, "provider_table.json": {"measured": "v", "search": [dict(FULL)]}}
    monkeypatch.setattr(providers_module, "read_release", lambda name, **_: files[name])
    shipped_providers.cache_clear()
    yield files
    shipped_providers.cache_clear()


@pytest.mark.parametrize(
    "row",
    [
        {"id": 1},
        {"id": "a"},
        {"id": "extract:a"},
        {"quality": "high"},
        {"quality": None},
        {"ci95": -1},
        {"usd_per_1k": True},
        {"p50_s": float("inf")},
        {"quality": 10**400},  # too large for a float: refused, not an OverflowError
        {"quality": float("nan")},
    ],
)
def test_a_row_without_a_search_id_and_four_finite_measures_is_refused(
    row: dict[str, Any], release: dict[str, Any]
) -> None:
    release["provider_table.json"]["search"] = [FULL | row]
    with pytest.raises(ConfigurationError, match="needs a search candidate's id and four measures, each a finite"):
        shipped_providers()


def test_a_good_row_is_read_and_an_id_named_twice_is_refused(release: dict[str, Any]) -> None:
    assert shipped_providers() == ProviderTable("v", (Point("search:a", 1.0, 1.0, 1.0, 1.0),))
    other = FULL | {"id": "search:b"}
    assert [point.id for point in checked_points([FULL, other])] == ["search:a", "search:b"]
    with pytest.raises(ConfigurationError) as refused:
        checked_points([FULL, other, FULL])
    assert str(refused.value) == NOT_USABLE + "it names ['search:a'] more than once."


@pytest.mark.parametrize(
    ("table", "error"),
    [
        ({"search": [FULL]}, "KeyError"),
        ({"measured": "v", "search": None}, "TypeError"),
        ({"measured": "v", "search": ["row"]}, "AttributeError"),
        (["measured", "search"], "TypeError"),
    ],
)
def test_a_damaged_provider_table_is_a_configuration_error(table: Any, error: str, release: dict[str, Any]) -> None:
    release["provider_table.json"] = table
    with pytest.raises(ConfigurationError) as refused:
        shipped_providers()
    assert str(refused.value) == NOT_USABLE + f"it could not be read ({error})."


def test_a_manifest_that_names_no_table_or_names_it_badly(release: dict[str, Any]) -> None:
    release["manifest.json"] = {"provider_table": {"file": "provider_table.json"}}
    with pytest.raises(ConfigurationError, match=r"could not be read \(KeyError\)"):
        shipped_providers()
    release["manifest.json"] = {}
    shipped_providers.cache_clear()
    assert shipped_providers() == ProviderTable(), "a release that names no provider table ranks no backends"


def test_a_provider_table_that_is_not_the_one_the_manifest_names_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    real = providers_module.read_release

    def altered(name: str, **kwargs: Any) -> Any:
        data = real(name, **kwargs)
        if name == DATA_DRIVEN_MANIFEST_FILE:
            data["provider_table"]["sha256"] = "0" * 64
        return data

    monkeypatch.setattr(providers_module, "read_release", altered)
    shipped_providers.cache_clear()
    try:
        with pytest.raises(ConfigurationError) as refused:
            shipped_providers()
        assert str(refused.value) == NOT_USABLE + "its checksum does not match the manifest."
    finally:
        shipped_providers.cache_clear()


@pytest.mark.parametrize("error", [json.JSONDecodeError("bad", "{", 0), FileNotFoundError("gone")])
def test_a_release_file_that_cannot_be_read_is_a_provider_table_error(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable(name: str, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(providers_module, "read_release", unreadable)
    shipped_providers.cache_clear()
    try:
        with pytest.raises(ConfigurationError, match="provider table is not usable"):
            shipped_providers()
    finally:
        shipped_providers.cache_clear()
