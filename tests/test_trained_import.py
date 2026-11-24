"""Delivered provenance is preserved, and inconsistent counts cannot replace a release."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import trained_router as importer


def _delivery(tmp_path: Path, counts: list[Any]) -> tuple[Path, dict[str, Any]]:
    source = tmp_path / "delivery"
    table = source / importer.SOURCE_TABLE
    table.parent.mkdir(parents=True)
    data = {
        "version": "0.1.0",
        "trained_at": "2026-11-24",
        "sources": [
            {"name": str(index), "version": "v1", "url": None, "license": "own", "examples": count}
            for index, count in enumerate(counts)
        ],
        "tables": [{"": [3, 3]}],
    }
    table.write_text(json.dumps(data), encoding="utf-8")
    return source, data


def test_import_preserves_delivered_metadata_and_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, data = _delivery(tmp_path, [1, 2])
    output = tmp_path / "output"
    monkeypatch.setattr(importer, "DATA", output)
    importer.write_release(source, "0.1.0")
    manifest = json.loads((output / importer.TRAINED_MANIFEST_FILE).read_text())
    content = (output / importer.TABLE_FILE).read_bytes()
    assert content == (source / importer.SOURCE_TABLE).read_bytes()
    assert manifest["tool_table"] == {
        "file": importer.TABLE_FILE,
        "sha256": hashlib.sha256(content).hexdigest(),
        "requests": 3,
        **{name: data[name] for name in ("version", "trained_at", "sources")},
    }


@pytest.mark.parametrize("counts", [[1, 1], [True, 2], [-1, 4], [1.5, 1.5], ["1", 2]])
def test_bad_source_counts_leave_existing_release_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counts: list[Any]
) -> None:
    source, _ = _delivery(tmp_path, counts)
    output = tmp_path / "output"
    output.mkdir()
    table = output / importer.TABLE_FILE
    manifest = output / importer.TRAINED_MANIFEST_FILE
    table.write_bytes(b"existing table")
    manifest.write_bytes(b"existing manifest")
    golden = tmp_path / "golden.gz"
    golden.write_bytes(b"existing golden")
    monkeypatch.setattr(importer, "DATA", output)
    monkeypatch.setattr(importer, "GOLDEN", golden)
    monkeypatch.setattr(importer, "golden_rows", lambda source: [])
    monkeypatch.setattr(importer.sys, "argv", ["trained-router", "--source", str(source), "--version", "0.1.0"])
    with pytest.raises(ValueError, match="source examples"):
        importer.main()
    assert (table.read_bytes(), manifest.read_bytes()) == (b"existing table", b"existing manifest")
    assert golden.read_bytes() == b"existing golden"
