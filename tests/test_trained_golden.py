"""The trained router against its golden file: what the training code returned for two thousand requests."""

from __future__ import annotations

import gzip
import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

from scripts.trained_prompts import prompts
from web_router.routing.trained.features import featurize
from web_router.routing.trained.table import shipped_table

GOLDEN = Path(__file__).parent / "data" / "trained" / "golden.jsonl.gz"
DATA = resources.files("web_router") / "data" / "trained"


def _golden() -> list[list[Any]]:
    return [json.loads(line) for line in gzip.decompress(GOLDEN.read_bytes()).decode("utf-8").splitlines()]


def test_features_and_table_agree_with_the_training_code_on_every_golden_request() -> None:
    table = shipped_table()
    wrong = []
    for prompt, values, tool in _golden():
        counts = table.row(prompt).counts
        got = (featurize(prompt, table.features), max(counts, key=counts.__getitem__))
        if got != (values, tool):
            wrong.append((prompt, values, tool, got))
    assert not wrong, f"{len(wrong)} requests differ from training, first: {wrong[0]}"


def test_the_golden_file_covers_every_feature_value_and_every_full_row_of_the_table() -> None:
    table, rows = shipped_table(), _golden()
    for index in range(len(table.features)):
        in_table = {key.split("|")[index] for key in table.tables[-1]}
        assert in_table <= {values[index] for _, values, _ in rows}
    missed = set(table.tables[-1]) - {"|".join(values) for _, values, _ in rows}
    assert not missed and {tool for *_, tool in rows} == set(table.labels)


def test_the_golden_file_was_built_from_the_current_request_list() -> None:
    assert [prompt for prompt, *_ in _golden()] == prompts(), "run make trained-router after changing the list"


def test_the_manifest_names_the_shipped_table_by_its_checksum() -> None:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    table = (DATA / manifest["tool_table"]["file"]).read_bytes()
    assert manifest["tool_table"]["sha256"] == hashlib.sha256(table).hexdigest()
    assert manifest["tool_table"]["requests"] == json.loads(table)["tables"][0][""][-1]
    assert shipped_table().version == manifest["version"]
