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

import argparse
import gzip
import hashlib
import io
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from _common import COMBINE_KEYS, DEPTH_MARK, _local, split_label
from _frozen_checks import (
    Scan,
    answers_digest,
    check_complete,
    check_failed_rows,
    check_no_repeats,
    check_same_answers,
    check_same_questions,
    check_same_setup,
    failed_rows,
    measures,
    question_id,
    question_index,
    read_golds,
    read_run,
    require_resolved,
    require_same_answers,
)
from _frozen_mixed import MIXED, freeze_mixed, sample_index
from _tools_effort_plan import QUESTIONS as MIXED_QUESTIONS

HERE = Path(__file__).resolve().parent
FROZEN_DIR = HERE / "frozen"
JOINED_DIR = HERE / "results" / "frozen-joined"
SUMS_FILE = "SHA256SUMS"
MANIFEST_FILE = "manifest.json"
SCHEMA_VERSION = 1
SUFFIX = ".jsonl.gz"

FIELDS = ("set", "question_id", "provider", "method", "mode", "grade", "latency_s", "cost_usd", "error")
"""A frozen row's fields, in order. Nothing else: no question, answer or gold text, no raw message."""
DEFAULT_MODE = "default"
"""A row's ``mode`` when the scan asked for none: the backend's own default for that method."""

FROZEN_SETS: dict[str, dict[str, Any]] = {
    "sealqa-hard-254": {"questions": "sealqa-hard", "method": "search", "runs": ["sealqa-hard-254"]},
    "sealqa-hard-254-twin": {"questions": "sealqa-hard", "method": "search", "runs": ["sealqa-hard-254-twin"]},
    "simpleqa-300-v2": {
        "questions": "simpleqa",
        "method": "search",
        "runs": ["simpleqa-300-v2", "simpleqa-300-keenable", "simpleqa-300-deepseek"],
    },
    "answer-simpleqa-300": {"questions": "simpleqa", "method": "answer", "runs": ["answer-simpleqa-300"]},
    # After answer-simpleqa-300, whose rows it reuses for its easy questions.
    "tools-effort-60": {"questions": MIXED_QUESTIONS, "method": MIXED, "runs": ["tools-effort-60"]},
}
"""Each frozen set: the question set its scans sampled, the method they called, and the scan folders it is made of.

A set made of several scans is one scan with backends added later, as
``run.py --combine`` merges them: the scans must share what ``--combine``
requires for their method (``COMBINE_KEYS``), the same questions and the same
gold answers; a key a scan predates reads as missing.
"""
MSG_SUMS_MISMATCH = "{name}: SHA-256 {actual} does not match SHA256SUMS ({expected}); the file changed or is damaged"
MSG_SUMS_MISSING = "{name} is in {folder} but not in SHA256SUMS"
MSG_FILE_MISSING = "{name} is in SHA256SUMS but not in {folder}"


def split_setup(setup: str) -> tuple[str, str]:
    """A scan's backend label as ``(provider, mode)``: ``tavily:ultra-fast@deep`` is ``tavily``, ``ultra-fast@deep``."""
    provider, vendor_mode, depth = split_label(setup)
    mode = vendor_mode + (DEPTH_MARK + depth if depth else "")
    return provider, mode or DEFAULT_MODE


def frozen_row(set_name: str, method: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """One scan row as a frozen row: ids, grade, latency, stated cost and error class, nothing written in words."""
    provider, mode = split_setup(str(row["provider"]))
    return {
        "set": set_name,
        "question_id": question_id(str(row["question"])),
        "provider": provider,
        "method": method,
        "mode": mode,
        **measures(row),
    }


def manifest_entry(spec: Mapping[str, Any], first: Scan, rows: list[dict[str, Any]], runs: list[Any]) -> dict[str, Any]:
    """A set's entry in ``manifest.json``: its setup, sizes, backends, their failed rows and the scans it is made of."""
    setup = first[1]
    return {
        "questions": spec["questions"],
        "dataset": setup.get("dataset"),
        "method": spec["method"],
        "limit": setup.get("limit"),
        "seed": setup.get("seed"),
        "max_results": setup.get("max_results"),
        "judge": setup.get("judge"),
        "question_count": len(first[2]),
        "answers_sha256": answers_digest(first[2]),
        "row_count": len(rows),
        "providers": sorted({r["provider"] for r in rows}),
        "failed_rows": failed_rows(rows),
        "runs": runs,
    }


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
        rows += [frozen_row(set_name, spec["method"], r) for r in source]
        runs.append({"run": run, "started": setup.get("started"), "providers": setup["providers"], "rows": len(source)})
    assert first is not None
    check_no_repeats(set_name, rows)
    asked = {split_label(label)[0] for run in runs for label in run["providers"]}
    check_complete(set_name, rows, set(first[2]), asked)
    rows.sort(key=lambda r: (r["question_id"], r["provider"], r["mode"]))
    return rows, manifest_entry(spec, first, rows, runs)


def gzip_jsonl(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """Rows as gzipped JSON lines, byte for byte the same on every run: no file name, no timestamp."""
    text = "".join(json.dumps({key: row[key] for key in FIELDS}, ensure_ascii=False) + "\n" for row in rows)
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0, compresslevel=9) as handle:
        handle.write(text.encode("utf-8"))
    return buffer.getvalue()


def read_rows(path: Path) -> list[dict[str, Any]]:
    """A frozen file's rows."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_sums(folder: Path) -> dict[str, str]:
    """``SHA256SUMS`` as file name -> digest, in ``sha256sum`` format."""
    sums: dict[str, str] = {}
    for line in (folder / SUMS_FILE).read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            sums[name.lstrip("*")] = digest
    return sums


def verify(folder: Path = FROZEN_DIR) -> dict[str, str]:
    """Every frozen file checked against ``SHA256SUMS``, both ways, and the manifest's failed rows against the rows.

    The digests, or ``SystemExit`` on a fault.
    """
    sums = read_sums(folder)
    present = {path.name for path in folder.iterdir() if path.name != SUMS_FILE and path.is_file()}
    for name in sorted(present - set(sums)):
        raise SystemExit(MSG_SUMS_MISSING.format(name=name, folder=folder))
    for name, expected in sorted(sums.items()):
        if name not in present:
            raise SystemExit(MSG_FILE_MISSING.format(name=name, folder=folder))
        actual = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(MSG_SUMS_MISMATCH.format(name=name, actual=actual, expected=expected))
    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    for set_name, entry in manifest["sets"].items():
        check_failed_rows(set_name, entry, read_rows(folder / (set_name + SUFFIX)))
    return sums


def joined_row(row: Mapping[str, Any], question: Any, labels: Iterable[str]) -> dict[str, Any]:
    """A frozen row with its question, gold answer and the dataset's labels for it put back."""
    meta = question.metadata.get("metadata", question.metadata)
    meta = meta if isinstance(meta, dict) else {}
    return {**row, "question": question.question, "gold_answer": question.answer, **{k: meta.get(k) for k in labels}}


LABELS = {
    "simpleqa": ("topic", "answer_type"),
    "sealqa-hard": ("topic", "freshness", "search_results"),
    MIXED_QUESTIONS: ("group", "source", "urls"),
}
"""The labels ``join`` adds from each dataset: the breakdowns the scan reports used."""


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
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--frozen", type=Path, default=FROZEN_DIR, help=f"the frozen folder (default: {FROZEN_DIR})")
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="a local copy of a question set (simpleqa, sealqa-hard) in place of the download",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    joining = commands.add_parser("join", help="verify, then put the questions back from the public datasets")
    joining.add_argument("--out", type=Path, default=JOINED_DIR, help=f"where to write (default: {JOINED_DIR})")
    commands.add_parser(
        "verify", help="check every frozen file against SHA256SUMS, and the failed-row counts against the rows"
    )
    building = commands.add_parser("build", help="write the frozen folder anew from scan folders")
    building.add_argument("results", type=Path, help="the folder holding the scans named in FROZEN_SETS")
    building.add_argument("--no-join-check", action="store_true", help="skip resolving ids against the datasets")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
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
