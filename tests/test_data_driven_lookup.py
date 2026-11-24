"""The provider table's builder: which lookup rows ship, and under which candidate id."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import data_driven_router as importer
from scripts.data_driven_manifest import PROVIDER_FILE, left_out
from scripts.data_driven_providers import (
    WHY_ID_DIFFERS,
    WHY_ID_NOT_SHOWN,
    WHY_NO_BACKEND,
    WHY_NO_MODE,
    measured_mode,
    provider_table,
)
from tests.data_driven_lookup_helpers import lookup, row
from web_router.exceptions import ProviderNotInstalledError
from web_router.registry import get_provider_class
from web_router.routing.data_driven.providers import shipped_providers
from web_router.routing.data_driven.table import read_release


def _kept(*rows: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    made = provider_table(lookup(*rows))
    return [kept["id"] for kept in made["search"]], made["left_out"]


def test_every_shipped_row_is_found_again_with_no_id_given() -> None:
    named = read_release(importer.DATA_DRIVEN_MANIFEST_FILE)["provider_table"]
    shipped = read_release(named["file"], sha256=named["sha256"])["search"]
    arms = [kept["arm"].replace(".search@", " ").split() for kept in shipped]
    depths = {"brave": "lite"}
    rows = [row(name, effort) | {"call": {"search_depth": depths.get(name, "fast")}} for name, effort in arms]
    ids, left_out = _kept(*rows)
    assert (ids, left_out) == ([kept["id"] for kept in shipped], [])
    assert shipped_providers().measured == "2026-11-23"


def test_a_named_id_is_kept_only_when_it_sends_the_measured_request() -> None:
    wrong_tier = "search:parallel:parallel-advanced+fast"
    ids, left_out = _kept(
        row("parallel", "low", search_depth="fast", id="search:parallel:parallel-turbo+fast"),
        row("parallel", "low", search_depth="fast", id=wrong_tier),
        row("nimble", search_depth="fast", id="search:nimble:fast"),
        row("nimble", search_depth="lite", id="search:nimble:fast"),
        row("nimble", search_depth="fast", id="search:tavily:fast"),
        row("brave", search_depth="lite", id="search:brave"),
    )
    assert ids == ["search:parallel:parallel-turbo+fast", "search:nimble:fast", "search:brave"]
    assert [entry["why"] for entry in left_out] == [
        WHY_ID_DIFFERS.format(id=wrong_tier),
        WHY_ID_DIFFERS.format(id="search:nimble:fast"),
        WHY_ID_DIFFERS.format(id="search:tavily:fast"),
    ]


@pytest.mark.parametrize(
    ("name", "effort", "depth", "claimed"),
    [
        ("brave", "base", "fast", "search:brave:fast"),
        ("brave", "base", "lite", "search:brave:lite"),
        ("nimble", "base", "fast", "nimble:fast"),
        ("nimble", "base", "fast", "extract:nimble:fast"),
        ("parallel", "low", "fast", "search:parallel:fast+parallel-turbo"),
    ],
)
def test_a_named_id_that_is_not_the_one_a_router_is_shown_is_left_out(
    name: str, effort: str, depth: str, claimed: str
) -> None:
    ids, left_out = _kept(row(name, effort, search_depth=depth, id=claimed))
    assert (ids, [entry["why"] for entry in left_out]) == ([], [WHY_ID_NOT_SHOWN.format(id=claimed)])


def test_a_row_with_no_call_is_matched_at_the_fast_depth() -> None:
    assert _kept(row("nimble")) == (["search:nimble:fast"], []), "the depth changes what Nimble sends"


def test_a_row_that_ran_outside_web_router_is_left_out() -> None:
    ids, left_out = _kept(row("jina"), row("brave", id=None), row("brave"))
    assert ids == ["search:brave"], "with no id given, its default sends the measured request"
    assert left_out == [
        {"arm": "jina.search@base", "why": WHY_NO_BACKEND},
        {"arm": "brave.search@base", "why": WHY_NO_BACKEND},
    ]


class NoSuchMode(get_provider_class("brave")):  # type: ignore[misc]
    """A backend whose effort changes its request and that has no tier to send it by."""

    def _build_payload(self, request: Any) -> dict[str, Any]:
        return super()._build_payload(request) | {"effort": request.effort}


def test_a_backend_with_no_mode_for_the_measured_request_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    assert measured_mode(NoSuchMode(api_key="k"), "low") == (False, None)
    assert measured_mode(NoSuchMode(api_key="k"), None) == (True, None)
    monkeypatch.setattr("scripts.data_driven_providers.get_provider_class", lambda name: NoSuchMode)
    assert _kept(row("brave", "low")) == ([], [{"arm": "brave.search@low", "why": WHY_NO_MODE}])


def test_a_backend_this_environment_cannot_build_stops_the_import(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> Any:
        raise ProviderNotInstalledError(name, "all")

    monkeypatch.setattr("scripts.data_driven_providers.get_provider_class", missing)
    with pytest.raises(ProviderNotInstalledError):
        provider_table(lookup(row("brave")))


@pytest.mark.parametrize(
    ("content", "error"), [(None, FileNotFoundError), (b"{", json.JSONDecodeError), (b"{}", KeyError)]
)
def test_an_unreadable_left_out_report_is_not_reported_as_empty(
    tmp_path: Path, content: bytes | None, error: type[Exception]
) -> None:
    if content is not None:
        (tmp_path / PROVIDER_FILE).write_bytes(content)
    with pytest.raises(error):
        left_out(tmp_path)
