"""Delivered provenance is preserved, and inconsistent counts cannot replace a release."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import data_driven_router as importer
from scripts.data_driven_manifest import PROVIDER_FILE
from tests.data_driven_lookup_helpers import lookup as lookup_table
from tests.data_driven_lookup_helpers import row

LOOKUP = lookup_table(row("brave"))


def _delivery(tmp_path: Path, counts: list[Any]) -> tuple[Path, dict[str, Any]]:
    source = tmp_path / "delivery"
    table = source / importer.SOURCE_TABLE
    table.parent.mkdir(parents=True)
    data = {
        "version": "0.1.0",
        "features": [],
        "labels": ["search"],
        "min_count": 1,
        "trained_at": "2026-11-24",
        "sources": [
            {"name": str(index), "version": "v1", "url": None, "license": "own", "examples": count}
            for index, count in enumerate(counts)
        ],
        "tables": [{"": [3, 3]}],
    }
    table.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return source, data


def test_import_preserves_delivered_metadata_and_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, data = _delivery(tmp_path, [1, 2])
    output = tmp_path / "output"
    monkeypatch.setattr(importer, "DATA", output)
    importer.write_release(source, "0.1.0")
    manifest = json.loads((output / importer.DATA_DRIVEN_MANIFEST_FILE).read_text())
    content = (output / importer.TABLE_FILE).read_bytes()
    assert content == (source / importer.SOURCE_TABLE).read_bytes()
    assert manifest["tool_table"] == {
        "file": importer.TABLE_FILE,
        "sha256": hashlib.sha256(content).hexdigest(),
        "requests": 3,
        **{name: data[name] for name in ("version", "trained_at", "sources")},
    }


def _installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    output = tmp_path / "output"
    output.mkdir()
    table, manifest = output / importer.TABLE_FILE, output / importer.DATA_DRIVEN_MANIFEST_FILE
    table.write_bytes(b"existing table")
    manifest.write_bytes(b'{"existing": "manifest"}')
    golden = tmp_path / "golden.gz"
    golden.write_bytes(b"existing golden")
    monkeypatch.setattr(importer, "DATA", output)
    monkeypatch.setattr(importer, "GOLDEN", golden)
    return table, manifest, golden


def _assert_unchanged(files: tuple[Path, Path, Path]) -> None:
    assert tuple(path.read_bytes() for path in files) == (
        b"existing table",
        b'{"existing": "manifest"}',
        b"existing golden",
    )


@pytest.mark.parametrize("counts", [[1, 1], [True, 2], [-1, 4], [1.5, 1.5], ["1", 2]])
def test_bad_source_counts_leave_existing_release_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counts: list[Any]
) -> None:
    source, _ = _delivery(tmp_path, counts)
    files = _installed(tmp_path, monkeypatch)
    monkeypatch.setattr(importer, "golden_rows", lambda source: [])
    monkeypatch.setattr(importer.sys, "argv", ["data-driven-router", "--source", str(source), "--version", "0.1.0"])
    with pytest.raises(ValueError, match="source examples"):
        importer.main()
    _assert_unchanged(files)


def test_a_conflicting_version_cannot_replace_the_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, _ = _delivery(tmp_path, [1, 2])
    files = _installed(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="version must match"):
        importer.import_release(source, "0.2.0", [])
    _assert_unchanged(files)


def test_a_failed_golden_write_cannot_replace_the_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, _ = _delivery(tmp_path, [1, 2])
    files = _installed(tmp_path, monkeypatch)
    write_bytes = Path.write_bytes

    def fail_staged_golden(path: Path, content: bytes) -> int:
        if path.name == files[2].name and path != files[2]:
            raise OSError("golden write failed")
        return write_bytes(path, content)

    monkeypatch.setattr(Path, "write_bytes", fail_staged_golden)
    with pytest.raises(OSError, match="golden write failed"):
        importer.import_release(source, "0.1.0", [])
    _assert_unchanged(files)


def test_different_training_outputs_cannot_replace_the_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, _ = _delivery(tmp_path, [1, 2])
    files = _installed(tmp_path, monkeypatch)
    assert len(importer.import_release(source, "0.1.0", [["weather", [], "research"]])) == 1
    _assert_unchanged(files)


def test_a_matching_staged_release_publishes_the_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, _ = _delivery(tmp_path, [1, 2])
    table, manifest, golden = _installed(tmp_path, monkeypatch)
    rows = [["weather", [], "search"]]
    assert importer.import_release(source, "0.1.0", rows) == []
    assert table.read_bytes() == (source / importer.SOURCE_TABLE).read_bytes()
    assert json.loads(manifest.read_bytes())["version"] == "0.1.0"
    assert [json.loads(line) for line in gzip.decompress(golden.read_bytes()).splitlines()] == rows


@pytest.mark.parametrize(("existing", "with_lookup"), [(True, False), (False, False), (True, True), (False, True)])
def test_a_failed_replacement_rolls_back_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool, with_lookup: bool
) -> None:
    source, _ = _delivery(tmp_path, [1, 2])
    files = _installed(tmp_path, monkeypatch)
    provider_table = files[0].parent / PROVIDER_FILE
    provider_table.write_bytes(b"existing providers")
    lookup = tmp_path / "lookup.json"
    lookup.write_text(json.dumps(LOOKUP), encoding="utf-8")
    if not existing:
        for path in (*files, provider_table):
            path.unlink()
    replace = Path.replace

    def fail_golden_replacement(path: Path, destination: Path) -> Path:
        if destination == files[2]:
            raise OSError("golden replacement failed")
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_golden_replacement)
    with pytest.raises(OSError, match="golden replacement failed"):
        importer.import_release(source, "0.1.0", [["weather", [], "search"]], lookup if with_lookup else None)
    if existing:
        _assert_unchanged(files)
        assert provider_table.read_bytes() == b"existing providers"
    else:
        assert not any(path.exists() for path in (*files, provider_table))


@pytest.mark.parametrize("entry", ["provider_table.json", {}])
def test_a_malformed_named_provider_table_cannot_drop_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: Any
) -> None:
    source, _ = _delivery(tmp_path, [1, 2])
    files = _installed(tmp_path, monkeypatch)
    files[1].write_text(json.dumps({"provider_table": entry}), encoding="utf-8")
    before = tuple(path.read_bytes() for path in files)
    with pytest.raises(ValueError, match="names the provider table"):
        importer.import_release(source, "0.1.0", [])
    assert tuple(path.read_bytes() for path in files) == before


def test_changed_tool_bytes_need_a_higher_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, data = _delivery(tmp_path, [1, 2])
    files = _installed(tmp_path, monkeypatch)
    importer.import_release(source, "0.1.0", [])
    before = tuple(path.read_bytes() for path in files)
    data["trained_at"] = "2030-01-01"
    (source / importer.SOURCE_TABLE).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="tool table differs from the shipped one"):
        importer.import_release(source, "0.1.0", [])
    assert tuple(path.read_bytes() for path in files) == before
    data["version"] = "0.1.1"
    (source / importer.SOURCE_TABLE).write_text(json.dumps(data), encoding="utf-8")
    importer.import_release(source, "0.1.1", [])
    assert json.loads(files[1].read_bytes())["version"] == "0.1.1"
