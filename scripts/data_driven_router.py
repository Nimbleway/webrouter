"""Import a data-driven router release: ``make data-driven-router SOURCE=<folder> VERSION=0.2.0 [LOOKUP=<file>]``.

``SOURCE`` is the folder a training run delivers: a ``webrouter`` package with ``features.py``, ``router1.py``
and ``artifacts/router1.json``. This script

1. runs that code on the fixed requests of :mod:`scripts.data_driven_prompts` and writes what it returned to
   ``tests/data/data_driven/golden.jsonl.gz``;
2. copies the table to ``src/web_router/data/data_driven/tool_table.json`` and writes ``manifest.json`` beside it,
   with the version, table checksum and delivered training-source metadata;
3. with ``LOOKUP``, the delivered lookup table, writes ``provider_table.json``: each search row web-router can
   run as measured, by candidate id (:mod:`scripts.data_driven_providers`), named in the manifest by checksum.
   Without it the shipped provider table stays. ``VERSION`` is the delivered table's own; to refresh the provider
   table alone, pass the shipped ``SOURCE`` again with ``LOOKUP`` and a higher version;
4. replays the golden file against web-router's own features and table, and exits non-zero with the first
   differences when they disagree: then ``routing/data_driven/patterns.py`` no longer matches the training code
   and has to be updated from it.

The training code is imported and run, so only point this at a delivery you trust.
"""

from __future__ import annotations

import argparse
import gzip
import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scripts.data_driven_manifest import (
    PROVIDER_ENTRY,
    PROVIDER_FILE,
    TABLE_FILE,
    carried,
    check_provider_version,
    check_version,
    left_out,
    provider_entry,
    shipped,
    tool_entry,
)
from scripts.data_driven_prompts import prompts
from web_router.constants_data_driven import (
    DATA_DRIVEN_DATA_DIR,
    DATA_DRIVEN_MANIFEST_FILE,
    DATA_DRIVEN_ROUTER_NAME,
    DATA_DRIVEN_SCHEMA,
)
from web_router.routing.data_driven.table import ToolTable, checked_table

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT.joinpath("src", "web_router", *DATA_DRIVEN_DATA_DIR)
GOLDEN = ROOT / "tests" / "data" / "data_driven" / "golden.jsonl.gz"
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


def write_release(source: Path, version: str, *, destination: Path | None = None, lookup: Path | None = None) -> None:
    """Copy the tool table, build the provider table from ``lookup`` when given, and write the manifest naming them.

    With no ``lookup``, the shipped provider table is kept as it is.
    """
    table = (source / SOURCE_TABLE).read_bytes()
    entry, before = tool_entry(table), shipped(DATA)
    check_version(entry, version, before, lookup is not None)
    destination = DATA if destination is None else destination
    destination.mkdir(parents=True, exist_ok=True)
    (destination / TABLE_FILE).write_bytes(table)
    manifest = {
        "_about": "The data-driven router's release: one version for its data files and the features they were "
        "trained with. Written by `make data-driven-router`; do not edit by hand.",
        "router": DATA_DRIVEN_ROUTER_NAME,
        "version": version,
        "schema": DATA_DRIVEN_SCHEMA,
        "built": datetime.now(UTC).date().isoformat(),
        "tool_table": entry,
    }
    providers = provider_entry(lookup, destination) if lookup is not None else carried(before, DATA)
    if providers is not None:
        check_provider_version(before, providers, version)
        manifest[PROVIDER_ENTRY] = providers
    (destination / DATA_DRIVEN_MANIFEST_FILE).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def differences(rows: list[list[Any]], *, table: ToolTable | None = None) -> list[str]:
    """Where web-router's own features or table disagree with the training code, one line each."""
    from web_router.routing.data_driven.features import featurize
    from web_router.routing.data_driven.table import shipped_table

    if table is None:
        shipped_table.cache_clear()
        table = shipped_table()
    out = []
    for prompt, values, tool in rows:
        counts = table.row(prompt).counts
        got = (featurize(prompt, table.features), max(counts, key=counts.__getitem__))
        if got != (values, tool):
            out.append(f"{prompt!r}: training gave {values} -> {tool}, web-router gives {got[0]} -> {got[1]}")
    return out


def _backup(outputs: list[tuple[Path, Path]], staged: Path) -> dict[Path, Path]:
    backups = {}
    for index, (_, destination) in enumerate(outputs):
        if destination.exists():
            backup = staged / f"previous-{index}"
            backup.write_bytes(destination.read_bytes())
            backups[destination] = backup
    return backups


def _publish(outputs: list[tuple[Path, Path]], backups: dict[Path, Path]) -> None:
    published = []
    try:
        for artifact, destination in outputs:
            artifact.replace(destination)
            published.append(destination)
    except OSError:
        for destination in reversed(published):
            if backup := backups.get(destination):
                backup.replace(destination)
            else:
                destination.unlink()
        raise


def import_release(source: Path, version: str, rows: list[list[Any]], lookup: Path | None = None) -> list[str]:
    """Stage and compare every output before replacing any file in the existing release."""
    with TemporaryDirectory(dir=DATA.parent) as temporary:
        staged = Path(temporary)
        write_release(source, version, destination=staged, lookup=lookup)
        lines = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        golden = staged / GOLDEN.name
        golden.write_bytes(gzip.compress(lines.encode("utf-8"), mtime=0))
        data = json.loads((staged / TABLE_FILE).read_bytes())
        wrong = differences(rows, table=checked_table(version, data))
        if wrong:
            return wrong
        DATA.mkdir(parents=True, exist_ok=True)
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        names = (TABLE_FILE, DATA_DRIVEN_MANIFEST_FILE, *((PROVIDER_FILE,) if lookup is not None else ()))
        outputs = [(staged / name, DATA / name) for name in names]
        outputs.append((golden, GOLDEN))
        _publish(outputs, _backup(outputs, staged))
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help="the delivered folder holding webrouter/")
    parser.add_argument("--version", required=True, help="the release's version, such as 0.2.0")
    parser.add_argument("--lookup", type=Path, help="the delivered lookup table, to rebuild the provider table")
    args = parser.parse_args()
    rows = golden_rows(args.source.resolve())
    wrong = import_release(args.source.resolve(), args.version, rows, args.lookup)
    for line in wrong[:SHOWN]:
        print(line, file=sys.stderr)
    for row in left_out(DATA) if args.lookup is not None and not wrong else ():
        print(f"left out {row['arm']}: {row['why']}", file=sys.stderr)
    print(f"data-driven router {args.version}: {len(rows)} golden requests, {len(wrong)} differences")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
