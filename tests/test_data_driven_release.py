"""Runtime validation of an installed trained release, including valid JSON with altered counts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from web_router.constants_data_driven import (
    DATA_DRIVEN_DATA_DIR,
    DATA_DRIVEN_MANIFEST_FILE,
    MSG_DATA_DRIVEN_CHECKSUM,
    MSG_DATA_DRIVEN_MIN_COUNT,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.data_driven import table as table_module
from web_router.routing.data_driven.table import shipped_table


@pytest.fixture
def release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    folder = tmp_path.joinpath(*DATA_DRIVEN_DATA_DIR)
    folder.mkdir(parents=True)
    monkeypatch.setattr(table_module.resources, "files", lambda package: tmp_path)
    shipped_table.cache_clear()
    try:
        yield folder
    finally:
        shipped_table.cache_clear()


def _write_release(folder: Path, min_count: Any = 20) -> Path:
    content = json.dumps(
        {"features": [], "labels": ["search"], "min_count": min_count, "tables": [{"": [1, 1]}]}
    ).encode()
    table = folder / "tools.json"
    table.write_bytes(content)
    (folder / DATA_DRIVEN_MANIFEST_FILE).write_text(
        json.dumps(
            {
                "schema": 1,
                "version": "9.9.9",
                "tool_table": {"file": table.name, "sha256": hashlib.sha256(content).hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    return table


def test_loading_a_release_checks_the_table_bytes(release: Path) -> None:
    table = _write_release(release)
    table.write_text(table.read_text().replace("[1, 1]", "[2, 2]"), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=MSG_DATA_DRIVEN_CHECKSUM):
        shipped_table()


@pytest.mark.parametrize("min_count", [0, -1, True, 1.5, "20", None, float("nan"), float("inf")])
def test_loading_a_release_refuses_invalid_thresholds(release: Path, min_count: Any) -> None:
    _write_release(release, min_count)
    with pytest.raises(ConfigurationError, match=MSG_DATA_DRIVEN_MIN_COUNT):
        shipped_table()


def test_loading_a_matching_release_preserves_its_version_and_threshold(release: Path) -> None:
    _write_release(release)
    table = shipped_table()
    assert (table.version, table.min_count, table.row("weather").counts) == ("9.9.9", 20, {"search": 1})


@pytest.mark.parametrize("broken", ["missing", "not json", "no table entry"])
def test_a_release_that_cannot_be_read_says_so_and_names_the_way_out(release: Path, broken: str) -> None:
    if broken == "not json":
        (release / DATA_DRIVEN_MANIFEST_FILE).write_text("{", encoding="utf-8")
    elif broken == "no table entry":
        (release / DATA_DRIVEN_MANIFEST_FILE).write_text('{"schema": 1, "version": "9.9.9"}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match=r"could not be read .*router=None runs without it"):
        shipped_table()
