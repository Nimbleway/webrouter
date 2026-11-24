from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from scripts import data_driven_router as importer
from scripts.data_driven_manifest import PROVIDER_FILE
from scripts.data_driven_providers import WHY_NO_BACKEND
from tests.data_driven_lookup_helpers import lookup, row
from web_router.exceptions import ConfigurationError

SHIPPED = resources.files("web_router").joinpath("data", "data_driven")


@pytest.fixture
def release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """A delivery holding the shipped tool table, a lookup table, and an empty folder to publish into."""
    source, output = tmp_path / "delivery", tmp_path / "release"
    (source / importer.SOURCE_TABLE).parent.mkdir(parents=True)
    (source / importer.SOURCE_TABLE).write_bytes(SHIPPED.joinpath(importer.TABLE_FILE).read_bytes())
    table = tmp_path / "lookup.json"
    table.write_text(json.dumps(lookup(row("brave"), row("jina"))), encoding="utf-8")
    monkeypatch.setattr(importer, "DATA", output)
    monkeypatch.setattr(importer, "GOLDEN", tmp_path / "golden.gz")
    return source, output, table


def _manifest(output: Path) -> dict[str, Any]:
    return dict(json.loads((output / importer.DATA_DRIVEN_MANIFEST_FILE).read_bytes()))


def _published(output: Path) -> tuple[Any, bytes]:
    return _manifest(output)["provider_table"], (output / PROVIDER_FILE).read_bytes()


def test_the_import_publishes_the_provider_table_and_names_it_by_checksum(release: tuple[Path, Path, Path]) -> None:
    source, output, table = release
    assert importer.import_release(source, "0.1.1", [], table) == []
    named, written = _published(output)
    assert named == {"file": PROVIDER_FILE, "sha256": hashlib.sha256(written).hexdigest(), "measured": "v"}
    assert [kept["id"] for kept in json.loads(written)["search"]] == ["search:brave"]
    assert json.loads(written)["left_out"] == [{"arm": "jina.search@base", "why": WHY_NO_BACKEND}]


def test_main_with_a_lookup_publishes_it_and_lists_what_it_left_out(
    release: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source, output, table = release
    monkeypatch.setattr(importer, "golden_rows", lambda source: [])
    argv = ["data-driven-router", "--source", str(source), "--version", "0.1.1", "--lookup", str(table)]
    monkeypatch.setattr(importer.sys, "argv", argv)
    assert importer.main() == 0
    said = capsys.readouterr()
    assert said.err == f"left out jina.search@base: {WHY_NO_BACKEND}\n"
    assert said.out == "data-driven router 0.1.1: 0 golden requests, 0 differences\n"
    assert _published(output)[0]["measured"] == "v"


def test_an_import_with_no_lookup_keeps_the_shipped_provider_table(release: tuple[Path, Path, Path]) -> None:
    source, output, table = release
    assert "provider_table" not in (importer.import_release(source, "0.1.1", []) or _manifest(output))
    importer.import_release(source, "0.1.2", [], table)
    before, before_entry = _published(output), _manifest(output)["provider_table"]
    importer.import_release(source, "0.1.2", [])
    assert _published(output) == before
    (output / PROVIDER_FILE).unlink()
    with pytest.raises(ValueError, match=r"names the provider table 'provider_table\.json', which is missing"):
        importer.import_release(source, "0.1.2", [])
    assert _manifest(output)["provider_table"] == before_entry, "a refused import leaves the release as it was"


def test_adding_a_provider_table_needs_a_higher_release_version(release: tuple[Path, Path, Path]) -> None:
    source, output, table = release
    importer.import_release(source, "0.1.1", [])
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(ValueError, match=r"differs from the shipped one.*higher than '0\.1\.1'"):
        importer.import_release(source, "0.1.1", [], table)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    importer.import_release(source, "0.1.2", [], table)
    assert (_manifest(output)["version"], _published(output)[0]["measured"]) == ("0.1.2", "v")


def test_a_shipped_manifest_that_cannot_be_read_stops_the_import(release: tuple[Path, Path, Path]) -> None:
    source, output, _ = release
    output.mkdir(parents=True, exist_ok=True)
    (output / importer.DATA_DRIVEN_MANIFEST_FILE).write_bytes(b"not json")
    with pytest.raises(ValueError, match="shipped manifest could not be read"):
        importer.import_release(source, "0.1.1", [])
    (output / importer.DATA_DRIVEN_MANIFEST_FILE).write_bytes(b"[]")
    with pytest.raises(ValueError, match="not an object"):
        importer.import_release(source, "0.1.1", [])


def test_a_changed_provider_table_needs_a_higher_release_version(release: tuple[Path, Path, Path]) -> None:
    source, output, table = release
    importer.import_release(source, "0.1.1", [], table)
    importer.import_release(source, "0.1.1", [], table)  # the same lookup again: the same table, the same version
    other = table.with_name("other.json")
    changed = json.loads(table.read_text(encoding="utf-8"))
    changed["version"] = "later"
    other.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match=r"differs from the shipped one.*higher than '0\.1\.1'"):
        importer.import_release(source, "0.1.1", [], other)
    importer.import_release(source, "0.1.2", [], other)
    assert (_manifest(output)["version"], _published(output)[0]["measured"]) == ("0.1.2", "later")


@pytest.mark.parametrize(
    "rows",
    [
        [row("brave") | {"quality": None}],
        [row("brave") | {"ci95": "wide"}],
        [row("brave") | {"usd_per_1k": -1.0}],
        [row("brave") | {"p50_s": float("inf")}],
        [{name: value for name, value in row("brave").items() if name != "quality"}],
        [row("brave"), row("brave", search_depth="lite", id="search:brave")],
    ],
)
def test_a_lookup_the_router_would_refuse_stops_the_import(
    rows: list[dict[str, Any]], release: tuple[Path, Path, Path]
) -> None:
    source, output, table = release
    importer.import_release(source, "0.1.1", [], table)
    before = _published(output)
    table.write_text(json.dumps(lookup(*rows)), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="provider table is not usable"):
        importer.import_release(source, "0.1.2", [], table)
    assert _published(output) == before and _manifest(output)["version"] == "0.1.1"


def test_a_lookup_alone_may_take_the_next_version_while_the_tool_table_is_unchanged(
    release: tuple[Path, Path, Path],
) -> None:
    source, output, table = release
    with pytest.raises(ValueError, match="version must match"):
        importer.import_release(source, "0.1.2", [], table)  # nothing shipped yet to be unchanged from
    importer.import_release(source, "0.1.1", [])
    with pytest.raises(ValueError, match="version must match"):
        importer.import_release(source, "0.1.2", [])  # no lookup table: nothing is refreshed
    assert importer.import_release(source, "0.1.2", [], table) == []
    manifest = _manifest(output)
    assert (manifest["version"], manifest["tool_table"]["version"]) == ("0.1.2", "0.1.1")
    delivered = json.loads((source / importer.SOURCE_TABLE).read_bytes())
    delivered["trained_at"] = "2030-01-01"
    (source / importer.SOURCE_TABLE).write_text(json.dumps(delivered), encoding="utf-8")
    with pytest.raises(ValueError, match="version must match"):
        importer.import_release(source, "0.1.3", [], table)  # the delivered table is no longer the shipped one
    assert _manifest(output)["version"] == "0.1.2"


def test_with_no_lookup_the_shipped_release_version_is_kept(release: tuple[Path, Path, Path]) -> None:
    source, output, table = release
    importer.import_release(source, "0.1.1", [])
    importer.import_release(source, "0.1.2", [], table)
    before = _published(output)
    assert importer.import_release(source, "0.1.2", []) == [], "the shipped release's version, nothing moves back"
    assert (_manifest(output)["version"], _published(output)) == ("0.1.2", before)
    with pytest.raises(ValueError, match="version must match"):
        importer.import_release(source, "0.1.3", [])
    with pytest.raises(ValueError, match=r"never moves back: '0\.1\.2' has shipped"):
        importer.import_release(source, "0.1.1", [])  # the table's own version, but below the shipped release
    assert _manifest(output)["version"] == "0.1.2"


@pytest.mark.parametrize("version", ["0.1.2", "0.1.1", "0.0.9", "0.1.10"])
def test_a_lookup_alone_takes_only_a_higher_version_than_the_shipped_release(
    version: str, release: tuple[Path, Path, Path]
) -> None:
    source, output, table = release
    importer.import_release(source, "0.1.1", [])
    importer.import_release(source, "0.1.2", [], table)
    if version == "0.1.10":
        assert importer.import_release(source, version, [], table) == [], "compared as numbers, not as text"
        return
    with pytest.raises(ValueError) as refused:
        importer.import_release(source, version, [], table)
    same = f"a lookup table alone takes a version higher than the shipped release's '0.1.2'; got {version!r}"
    back = f"a release never moves back: '0.1.2' has shipped, and the import asks for {version!r}"
    assert str(refused.value) == (same if version == "0.1.2" else back)
    assert _manifest(output)["version"] == "0.1.2"


@pytest.mark.parametrize(
    "version",
    ["next", "0.2", "0.1.2rc1", "v0.1.2", "0.1.2.0", "", "01.1.2", "0.01.2", "0.1.02", "1٢.0.0", "0.1٢.0", "0.1.1٢"],
)
def test_a_version_that_is_not_three_numbers_is_refused(version: str, release: tuple[Path, Path, Path]) -> None:
    source, output, table = release
    importer.import_release(source, "0.1.1", [])
    for given in (None, table):
        with pytest.raises(ValueError) as refused:
            importer.import_release(source, version, [], given)
        assert str(refused.value) == f"a release version is three numbers, x.y.z; got {version!r}"
    assert _manifest(output)["version"] == "0.1.1"
