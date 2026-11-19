"""The frozen results: the scans route-eval and the latency priors rest on, one row per question and backend.

A scan's own folder under ``results/`` is gitignored and holds the questions,
the gold answers and each backend's answer word for word. The frozen files in
``frozen/`` hold none of that text: a row names its question by
``question_id``, a hash of the question's text, and keeps the grade, the
latency, a stated cost and the class of any error. ``join`` puts the questions
back, from the public datasets the scans sampled.

    uv run --all-extras python benchmarks/providers/frozen.py join
    uv run --all-extras python benchmarks/providers/frozen.py verify
    uv run --all-extras python benchmarks/providers/frozen.py build path/to/results

A set made by ``tools_effort.py`` is ``mixed``: its rows call several methods,
each naming its own, and its manifest entry keeps the run's sample, where each
question sits in the public set it came from.

``join`` checks every file against ``frozen/SHA256SUMS``, downloads SimpleQA
and SealQA, and writes each set with its question, gold answer and labels to
``results/frozen-joined/`` (gitignored); it fails if any ``question_id`` does
not resolve. ``build`` is for whoever holds the scans: it reads their folders
and writes ``frozen/`` anew.

Experimental until 1.0: the schema may change, and ``manifest.json`` says
which version a file follows.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from _common import COMBINE_KEYS, _local, split_label
from _frozen_checks import (
    Scan,
    check_complete,
    check_no_repeats,
    check_same_answers,
    check_same_questions,
    check_same_setup,
    question_index,
    read_golds,
    read_run,
    require_resolved,
    require_same_answers,
)
from _frozen_cli import parse
from _frozen_files import FIELDS as FIELDS
from _frozen_files import FROZEN_DIR as FROZEN_DIR
from _frozen_files import JOINED_DIR as JOINED_DIR
from _frozen_files import MANIFEST_FILE as MANIFEST_FILE
from _frozen_files import SCHEMA_VERSION as SCHEMA_VERSION
from _frozen_files import SUFFIX as SUFFIX
from _frozen_files import SUMS_FILE as SUMS_FILE
from _frozen_files import gzip_jsonl
from _frozen_files import read_rows as read_rows
from _frozen_files import read_sums as read_sums
from _frozen_files import verify as verify
from _frozen_mixed import MIXED, freeze_mixed, sample_index
from _frozen_price import canonical_label
from _frozen_price import price_mode as price_mode
from _frozen_rows import LABELS, joined_row, manifest_entry
from _frozen_rows import frozen_row as frozen_row
from _frozen_rows import split_setup as split_setup
from _frozen_sets import FROZEN_SETS as FROZEN_SETS


def freeze_set(
    set_name: str, results: Path, built: Mapping[str, list[dict[str, Any]]] | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A set's frozen rows, sorted, and its manifest entry, from its scan folders under ``results``.

    Refuses scans that asked other questions, graded on other answers or ran
    another setup, a backend that answered one question twice, and one that
    has no row for some question.
    """
    spec = FROZEN_SETS[set_name]
    if spec["method"] == MIXED:
        return freeze_mixed(set_name, spec, results, built or {})
    rows: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    first: Scan | None = None
    for run in spec["runs"]:
        setup, source = read_run(results / run)
        scan = (run, setup, read_golds(set_name, run, source))
        first = first or scan
        check_same_questions(set_name, scan, first)
        check_same_answers(set_name, scan, first)
        check_same_setup(set_name, scan, first, COMBINE_KEYS[spec["method"]])
        relabeled = ({**r, "provider": canonical_label(run, r["provider"])} for r in source)
        rows += [frozen_row(set_name, spec["method"], r) for r in relabeled]
        providers = [canonical_label(run, label) for label in setup["providers"]]
        runs.append({"run": run, "started": setup.get("started"), "providers": providers, "rows": len(source)})
    assert first is not None
    check_no_repeats(set_name, rows)
    asked = {split_label(label)[0] for run in runs for label in run["providers"]}
    check_complete(set_name, rows, set(first[2]), asked)
    rows.sort(key=lambda r: (r["question_id"], r["provider"], r["mode"]))
    return rows, manifest_entry(spec, first, rows, runs)


def load_index(questions: str, local: Mapping[str, Path]) -> dict[str, Any]:
    """Every question of a public set, by id: downloaded, or read from a local copy named with ``--dataset``."""
    # Imported here so verify and the tests need no network and no evals extra.
    from web_router.evals import Dataset

    dataset = Dataset.for_set(questions, path=local.get(questions))
    return question_index(dataset.questions, questions)


def set_index(entry: Mapping[str, Any], local: Mapping[str, Path]) -> dict[str, Any]:
    """The questions a set's rows name, by id: from its public set, or a mixed set's from its sample."""
    if entry["method"] == MIXED:
        return sample_index(entry["sample"], local)
    return load_index(entry["questions"], local)


def join(folder: Path, out: Path, local: Mapping[str, Path]) -> dict[str, int]:
    """Each frozen set with its questions put back, written to ``out``; rows per set, or ``SystemExit`` on a miss."""
    verify(folder)
    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    indexes: dict[str, dict[str, Any]] = {}
    joined: dict[str, list[str]] = {}
    for set_name, entry in manifest["sets"].items():
        questions = entry["questions"]
        if questions not in indexes:
            indexes[questions] = set_index(entry, local)
        index = indexes[questions]
        rows = read_rows(folder / (set_name + SUFFIX))
        require_resolved(set_name, rows, index, questions)
        require_same_answers(set_name, entry, rows, index)
        labels = LABELS.get(questions, ())
        joined[set_name] = [
            json.dumps(joined_row(r, index[r["question_id"]], labels), ensure_ascii=False) for r in rows
        ]
    # Written only once every set resolved, so a failed join leaves nothing half done.
    out.mkdir(parents=True, exist_ok=True)
    for set_name, lines in joined.items():
        (out / (set_name + ".jsonl")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {set_name: len(lines) for set_name, lines in joined.items()}


def build(results: Path, folder: Path, local: Mapping[str, Path], *, check_join: bool = True) -> dict[str, Any]:
    """``frozen/`` written anew from the scans under ``results``: one file per set, the manifest and ``SHA256SUMS``.

    Unless ``check_join`` is off, every question id is first resolved against
    the public dataset, so ``join`` is known to put every question back.
    """
    folder.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "fields": list(FIELDS), "sets": {}}
    files: dict[str, bytes] = {}
    built: dict[str, list[dict[str, Any]]] = {}
    for set_name in FROZEN_SETS:
        rows, entry = freeze_set(set_name, results, built)
        built[set_name] = rows
        if check_join:
            index = set_index(entry, local)
            entry["resolved"] = require_resolved(set_name, rows, index, entry["questions"])
            require_same_answers(set_name, entry, rows, index)
        manifest["sets"][set_name] = entry
        files[set_name + SUFFIX] = gzip_jsonl(rows)
    files[MANIFEST_FILE] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    for name, data in files.items():
        (folder / name).write_bytes(data)
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items()))
    (folder / SUMS_FILE).write_text(sums, encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> None:
    args = parse(__doc__, argv)
    local = _local(args.dataset)
    if args.command == "verify":
        print(f"{len(verify(args.frozen))} files match {args.frozen / SUMS_FILE}")
    elif args.command == "join":
        for set_name, count in join(args.frozen, args.out, local).items():
            print(f"{set_name}: {count} rows, every question resolved -> {args.out / (set_name + '.jsonl')}")
    else:
        manifest = build(args.results, args.frozen, local, check_join=not args.no_join_check)
        for set_name, entry in manifest["sets"].items():
            resolved = entry.get("resolved", "unchecked")
            print(f"{set_name}: {entry['row_count']} rows, {entry['question_count']} questions, resolved {resolved}")


if __name__ == "__main__":
    main()
