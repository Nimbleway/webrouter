"""The provider table's import: which lookup rows ship, under which candidate id, and how a release is versioned."""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from scripts import data_driven_router as importer
from scripts.data_driven_manifest import PROVIDER_FILE
from scripts.data_driven_providers import WHY_ID_DIFFERS, WHY_NO_BACKEND, WHY_NO_MODE, measured_mode, provider_table
from web_router.exceptions import ProviderNotInstalledError
from web_router.registry import get_provider_class
from web_router.routing.data_driven.providers import shipped_providers
from web_router.routing.data_driven.table import read_release

SHIPPED = resources.files("web_router").joinpath("data", "data_driven")
MEASURES = {"quality": 1.0, "ci95": 1.0, "usd_per_1k": 1.0, "p50_s": 1.0}


def _row(name: str, effort: str = "base", **call: Any) -> dict[str, Any]:
    row = {"arm": f"{name}.search@{effort}", "provider": name, "effort": effort, **MEASURES}
    return row | ({"call": {"provider": name, **call}} if call else {})


def _lookup(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"version": "v", "source": "s", "judge": "j", "tools": {"search": {"all": list(rows)}}}


def _kept(*rows: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    made = provider_table(_lookup(*rows))
    return [row["id"] for row in made["search"]], made["left_out"]


def test_every_shipped_row_is_found_again_with_no_id_given() -> None:
    named = read_release(importer.DATA_DRIVEN_MANIFEST_FILE)["provider_table"]
    shipped = read_release(named["file"], sha256=named["sha256"])["search"]
    arms = [row["arm"].replace(".search@", " ").split() for row in shipped]
    depths = {"brave": "lite"}
    rows = [_row(name, effort) | {"call": {"search_depth": depths.get(name, "fast")}} for name, effort in arms]
    ids, left_out = _kept(*rows)
    assert (ids, left_out) == ([row["id"] for row in shipped], [])
    assert shipped_providers().measured == "2026-11-23"


def test_a_named_id_is_kept_only_when_it_sends_the_measured_request() -> None:
    wrong_tier = "search:parallel:parallel-advanced+fast"
    ids, left_out = _kept(
        _row("parallel", "low", search_depth="fast", id="search:parallel:parallel-turbo+fast"),
        _row("parallel", "low", search_depth="fast", id=wrong_tier),
        _row("nimble", search_depth="fast", id="search:nimble:fast"),
        _row("nimble", search_depth="lite", id="search:nimble:fast"),
        _row("nimble", search_depth="fast", id="search:tavily:fast"),
        _row("brave", search_depth="lite", id="search:brave"),
    )
    assert ids == ["search:parallel:parallel-turbo+fast", "search:nimble:fast", "search:brave"]
    assert [row["why"] for row in left_out] == [
        WHY_ID_DIFFERS.format(id=wrong_tier),
        WHY_ID_DIFFERS.format(id="search:nimble:fast"),
        WHY_ID_DIFFERS.format(id="search:tavily:fast"),
    ]


def test_a_row_that_ran_outside_web_router_is_left_out() -> None:
    ids, left_out = _kept(_row("jina"), _row("brave", id=None), _row("brave"))
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
    assert _kept(_row("brave", "low")) == ([], [{"arm": "brave.search@low", "why": WHY_NO_MODE}])


def test_a_backend_this_environment_cannot_build_stops_the_import(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> Any:
        raise ProviderNotInstalledError(name, "all")

    monkeypatch.setattr("scripts.data_driven_providers.get_provider_class", missing)
    with pytest.raises(ProviderNotInstalledError):
        provider_table(_lookup(_row("brave")))


@pytest.fixture
def release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """A delivery holding the shipped tool table, a lookup table, and an empty folder to publish into."""
    source, output = tmp_path / "delivery", tmp_path / "release"
    (source / importer.SOURCE_TABLE).parent.mkdir(parents=True)
    (source / importer.SOURCE_TABLE).write_bytes(SHIPPED.joinpath(importer.TABLE_FILE).read_bytes())
    lookup = tmp_path / "lookup.json"
    lookup.write_text(json.dumps(_lookup(_row("brave"), _row("jina"))), encoding="utf-8")
    monkeypatch.setattr(importer, "DATA", output)
    monkeypatch.setattr(importer, "GOLDEN", tmp_path / "golden.gz")
    return source, output, lookup


def _manifest(output: Path) -> dict[str, Any]:
    return dict(json.loads((output / importer.DATA_DRIVEN_MANIFEST_FILE).read_bytes()))


def test_the_import_publishes_the_provider_table_and_names_it_by_checksum(release: tuple[Path, Path, Path]) -> None:
    source, output, lookup = release
    assert importer.import_release(source, "0.1.1", [], lookup) == []
    named, written = _manifest(output)["provider_table"], (output / PROVIDER_FILE).read_bytes()
    assert named == {"file": PROVIDER_FILE, "sha256": hashlib.sha256(written).hexdigest(), "measured": "v"}
    assert [row["id"] for row in json.loads(written)["search"]] == ["search:brave"]
    assert json.loads(written)["left_out"] == [{"arm": "jina.search@base", "why": WHY_NO_BACKEND}]


def test_an_import_with_no_lookup_keeps_the_shipped_provider_table(release: tuple[Path, Path, Path]) -> None:
    source, output, lookup = release
    assert "provider_table" not in (importer.import_release(source, "0.1.1", []) or _manifest(output))
    importer.import_release(source, "0.1.1", [], lookup)
    before = (_manifest(output)["provider_table"], (output / PROVIDER_FILE).read_bytes())
    importer.import_release(source, "0.1.1", [])
    assert (_manifest(output)["provider_table"], (output / PROVIDER_FILE).read_bytes()) == before
    (output / PROVIDER_FILE).unlink()
    importer.import_release(source, "0.1.1", [])
    assert "provider_table" not in _manifest(output), "an entry whose file is gone is not carried"


def test_a_lookup_alone_may_take_the_next_version_while_the_tool_table_is_unchanged(
    release: tuple[Path, Path, Path],
) -> None:
    source, output, lookup = release
    with pytest.raises(ValueError, match="version must match"):
        importer.import_release(source, "0.1.2", [], lookup)  # nothing shipped yet to be unchanged from
    importer.import_release(source, "0.1.1", [])
    with pytest.raises(ValueError, match="version must match"):
        importer.import_release(source, "0.1.2", [])  # no lookup table: nothing is refreshed
    assert importer.import_release(source, "0.1.2", [], lookup) == []
    manifest = _manifest(output)
    assert (manifest["version"], manifest["tool_table"]["version"]) == ("0.1.2", "0.1.1")
    table = json.loads((source / importer.SOURCE_TABLE).read_bytes())
    table["trained_at"] = "2030-01-01"
    (source / importer.SOURCE_TABLE).write_text(json.dumps(table), encoding="utf-8")
    with pytest.raises(ValueError, match="version must match"):
        importer.import_release(source, "0.1.3", [], lookup)  # the delivered table is no longer the shipped one
    assert _manifest(output)["version"] == "0.1.2"
