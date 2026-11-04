"""What the provider scans share: the statistics, the merging of finished scans and the entry point's skeleton.

``run.py`` scans search backends and ``extract.py`` extract backends. Each
builds its own rows, scores and report, and brings them here to be bounded,
combined and written the same way, so the two reports read alike. Imported
as a sibling, by name: a scan runs as a script from this folder, and the
tests put the folder on the path before loading one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections.abc import Awaitable, Callable, Hashable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_OUT = Path(__file__).resolve().parent / "results"
Z_95 = 1.96
MIN_GROUP = 10
"""A group with fewer inputs than this is shown but never names any backend's best: too few to tell them apart."""
APART = {None: "best", False: "not shown apart", True: "behind"}
"""The recommended order's last column: whether this run tells a backend from the best one."""
MSG_OUT_NOT_EMPTY = "{path} already exists; pass a new --out folder rather than mixing two runs in one."
MSG_REPEATED_BACKEND = "{folder} has {providers} again; each backend may appear in only one of the scans combined"
DEPTH_MARK = "@"
MODE_MARK = ":"
"""How a search scan names a backend in one of its modes: ``provider[:vendor-mode][@depth]``."""
SEARCH_COMBINE_KEYS = (
    "questions",
    "dataset_sha256",
    "question_column",
    "answer_column",
    "withheld",
    "limit",
    "seed",
    "max_results",
    "judge",
)
"""What two search scans must share for their rows to sit in one table: the same questions, search size and judge."""
ANSWER_COMBINE_KEYS = (
    "questions",
    "dataset_sha256",
    "question_column",
    "answer_column",
    "withheld",
    "limit",
    "seed",
    "judge",
    "baseline",
)
"""What two answer scans must share for their rows to sit in one table -- the search scan beside them too."""
COMBINE_KEYS = {"search": SEARCH_COMBINE_KEYS, "answer": ANSWER_COMBINE_KEYS}
"""The keys above by the method a scan called."""

Rows = list[dict[str, Any]]
"""One dict per input and backend, as ``rows.jsonl`` holds them."""
Setup = dict[str, Any]
"""What a scan was asked to do, as ``run.json`` holds it."""
Sink = Callable[[Rows], None]
"""Where a scan hands each input's rows the moment it has them, so a scan that dies keeps what it paid for.

The setup is written again with each batch, so what ``run`` added to it is
on disk with the rows.
"""


def _wilson(hits: int, n: int) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = hits / n
    denominator = 1 + Z_95**2 / n
    centre = (p + Z_95**2 / (2 * n)) / denominator
    half = Z_95 * math.sqrt(p * (1 - p) / n + Z_95**2 / (4 * n * n)) / denominator
    return (centre - half, centre + half)


def _pct(values: list[float], fraction: float) -> float | None:
    # Nearest rank, as evaluate() computes its own p95, so a scan agrees with the library on the same rows.
    values = sorted(values)
    return values[max(0, min(len(values) - 1, math.ceil(fraction * len(values)) - 1))] if values else None


def _p(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _s(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}s"


def split_label(label: str) -> tuple[str, str, str]:
    """``provider[:vendor-mode][@depth]`` as its three parts, each ``""`` when the label names none."""
    spec, _, depth = label.partition(DEPTH_MARK)
    provider, _, mode = spec.partition(MODE_MARK)
    return provider, mode, depth


def combine_scans(
    folders: list[Path],
    *,
    keys: Sequence[str],
    identity: Callable[[dict[str, Any]], Hashable],
    cannot_combine: str,
    other_rows: str,
    defaults: Mapping[str, Any] | None = None,
) -> tuple[Rows, Setup]:
    """Rows and setup of several scans as one -- say, backends added later on the same inputs.

    ``keys`` is what every scan's setup must share, ``identity`` what names
    a row's input, so scans of different inputs are refused, and
    ``defaults`` fills in a ``run.json`` written before a key existed.
    """
    rows: Rows = []
    setup: Setup = {}
    for folder in folders:
        theirs = {**(defaults or {}), **json.loads((folder / "run.json").read_text(encoding="utf-8"))}
        if not setup:
            setup = {**theirs, "providers": [], "combined_from": []}
        for key in keys:
            if theirs.get(key) != setup.get(key):
                raise SystemExit(
                    cannot_combine.format(folder=folder, key=key, theirs=theirs.get(key), ours=setup.get(key))
                )
        repeated = [p for p in theirs["providers"] if p in setup["providers"]]
        if repeated:
            raise SystemExit(MSG_REPEATED_BACKEND.format(folder=folder, providers=", ".join(repeated)))
        theirs_rows = [
            json.loads(line) for line in (folder / "rows.jsonl").read_text(encoding="utf-8").splitlines() if line
        ]
        if rows and {identity(r) for r in theirs_rows} != {identity(r) for r in rows}:
            raise SystemExit(other_rows.format(folder=folder))
        setup["providers"] += theirs["providers"]
        setup["combined_from"].append(str(folder))
        rows += theirs_rows
    return rows, setup


def scan_main(
    argv: list[str] | None,
    *,
    parse_args: Callable[[list[str]], argparse.Namespace],
    setup_of: Callable[[argparse.Namespace], Setup],
    run: Callable[[argparse.Namespace, Setup, Sink], Awaitable[Rows]],
    combine: Callable[[list[Path]], tuple[Rows, Setup]],
    summarize: Callable[[Rows, Setup], dict[str, Any]],
    markdown: Callable[[dict[str, Any]], str],
    out_prefix: str = "",
) -> None:
    """A scan's entry point: parse, then fetch or combine, then score and write, into a fresh ``--out`` folder.

    A scan is an hour of paid calls, so nothing waits for the end: ``run.json``
    is written before the first call, every input's rows are appended to
    ``rows.jsonl`` as ``run`` hands them to the sink, and ``run.json`` is
    written again with each batch, so what ``run`` added to the setup -- the
    inputs asked, the judge -- is on disk with the first rows. A scan that
    dies -- a dropped connection, Ctrl-C, one stray exception -- leaves both
    behind: ``--combine`` on that folder alone reports the rows it fetched,
    measured against the inputs asked. It cannot be merged with a finished
    scan, whose rows cover other inputs.
    """
    # An entry point, like the agent benchmark's: it may read the repo's .env,
    # and a variable already exported wins. The library itself never does.
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE)
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.out = args.out or DEFAULT_OUT / (out_prefix + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if (args.out / "rows.jsonl").exists():
        raise SystemExit(MSG_OUT_NOT_EMPTY.format(path=args.out / "rows.jsonl"))
    args.out.mkdir(parents=True, exist_ok=True)
    if args.combine:
        write_scan(args.out, *combine(args.combine), summarize=summarize, markdown=markdown)
        return
    setup = setup_of(args)
    write_setup(args.out, setup)
    with (args.out / "rows.jsonl").open("a", encoding="utf-8") as sink_file:

        def sink(rows: Rows) -> None:
            sink_file.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
            sink_file.flush()
            write_setup(args.out, setup)

        rows = asyncio.run(run(args, setup, sink))
    write_scan(args.out, rows, setup, summarize=summarize, markdown=markdown)


def write_setup(out: Path, setup: Setup) -> None:
    (out / "run.json").write_text(json.dumps(setup, indent=2), encoding="utf-8")


def write_scan(
    out: Path,
    rows: Rows,
    setup: Setup,
    *,
    summarize: Callable[[Rows, Setup], dict[str, Any]],
    markdown: Callable[[dict[str, Any]], str],
) -> None:
    """The four files every scan writes: its setup, its rows, the numbers for charts and the report, also printed.

    The rows file is rewritten whole from ``rows``: for a scan that ran, the
    same lines the sink appended; for a combine, the rows merged.
    """
    write_setup(out, setup)
    (out / "rows.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    summary = summarize(rows, setup)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report = markdown(summary)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
