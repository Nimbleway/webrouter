"""Import a trained router release: ``make trained-router SOURCE=<folder> VERSION=0.2.0``.

``SOURCE`` is the folder a training run delivers: a ``webrouter`` package with ``features.py``, ``router1.py``
and ``artifacts/router1.json``. This script

1. runs that code on the fixed requests of :mod:`scripts.trained_prompts` and writes what it returned to
   ``tests/data/trained/golden.jsonl.gz``;
2. copies the table to ``src/web_router/data/trained/tool_table.json`` and writes ``manifest.json`` beside it,
   with the version and the table's checksum;
3. replays the golden file against web-router's own features and table, and exits non-zero with the first
   differences when they disagree: then ``routing/trained/patterns.py`` no longer matches the training code
   and has to be updated from it.

The training code is imported and run, so only point this at a delivery you trust.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.trained_prompts import prompts

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "web_router" / "data" / "trained"
GOLDEN = ROOT / "tests" / "data" / "trained" / "golden.jsonl.gz"
TABLE_FILE = "tool_table.json"
SOURCE_TABLE = Path("webrouter") / "artifacts" / "router1.json"
SHOWN = 10


def golden_rows(source: Path) -> list[list[Any]]:
    """Each fixed request with the feature values and the method the training code gives it."""
    sys.path.insert(0, str(source))
    features = importlib.import_module("webrouter.features")
    router = importlib.import_module("webrouter.router1").Router1.load(source / SOURCE_TABLE, stage2_path=None)
    rows = []
    for prompt in prompts():
        values = features.featurize(prompt, router.features)
        rows.append([prompt, [values[name] for name in router.features], router.predict_table(prompt)])
    return rows


def write_release(source: Path, version: str) -> None:
    """Copy the table and write the manifest that names it."""
    DATA.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / SOURCE_TABLE, DATA / TABLE_FILE)
    table = (DATA / TABLE_FILE).read_bytes()
    manifest = {
        "_about": "The trained router's release: one version for its data files and the features they were "
        "trained with. Written by `make trained-router`; do not edit by hand.",
        "router": "trained",
        "version": version,
        "schema": 1,
        "built": datetime.now(UTC).date().isoformat(),
        "tool_table": {
            "file": TABLE_FILE,
            "sha256": hashlib.sha256(table).hexdigest(),
            "requests": json.loads(table)["tables"][0][""][-1],
        },
    }
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def differences(rows: list[list[Any]]) -> list[str]:
    """Where web-router's own features or table disagree with the training code, one line each."""
    from web_router.routing.trained.features import featurize
    from web_router.routing.trained.table import shipped_table

    shipped_table.cache_clear()
    table = shipped_table()
    out = []
    for prompt, values, tool in rows:
        counts = table.row(prompt).counts
        got = (featurize(prompt, table.features), max(counts, key=counts.__getitem__))
        if got != (values, tool):
            out.append(f"{prompt!r}: training gave {values} -> {tool}, web-router gives {got[0]} -> {got[1]}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help="the delivered folder holding webrouter/")
    parser.add_argument("--version", required=True, help="the release's version, such as 0.2.0")
    args = parser.parse_args()
    rows = golden_rows(args.source.resolve())
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    GOLDEN.write_bytes(gzip.compress(lines.encode("utf-8"), mtime=0))
    write_release(args.source.resolve(), args.version)
    wrong = differences(rows)
    for line in wrong[:SHOWN]:
        print(line, file=sys.stderr)
    print(f"trained router {args.version}: {len(rows)} golden requests, {len(wrong)} differences")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
