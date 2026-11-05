"""Fill the latency fields of ``web_router/data/prices.json`` from the frozen benchmark results.

Reads only what a fresh clone has: ``benchmarks/providers/frozen/``, checked
against its ``SHA256SUMS`` first. A row counts when the backend answered
(``error`` is null); on a failed row ``latency_s`` is the time to the failure.
Every frozen set that covers a (provider, method, mode) is pooled -- one run
drifts too much to stand alone -- and the row records how many calls that is
and which runs they came from. Two kinds of set are left out, so no question
counts twice: a rerun of an earlier set's questions by the same method, and a
mixed set, whose answer rows are copies of another set's. Search and answer only; every other row's
latency is left null, unknown.

    make latency                                 # rewrite the latency fields
    uv run python -m scripts.latency --check     # exit 1 if they are stale
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

from web_router.constants_prices import LATENCY_METHODS, MSG_LATENCY_STALE, PRICES_DATA_DIR, PRICES_DATA_FILE
from web_router.prices import price

ROOT = Path(__file__).resolve().parent.parent
PRICES_JSON = ROOT / "src" / "web_router" / PRICES_DATA_DIR / PRICES_DATA_FILE
BENCHMARKS = ROOT / "benchmarks" / "providers"

LATENCY_FIELDS = ("latency_p50_s", "latency_p90_s", "latency_n", "latency_runs", "latency_measured")
P50, P90 = 0.5, 0.9

Key = tuple[str, str, "str | None"]


SIBLINGS = ("_common", "_frozen_checks", "_frozen_mixed", "_tools_effort_plan")
"""What ``frozen.py`` imports from its folder by plain name; forgotten once it is loaded, as the tests do."""


def _benchmarks() -> tuple[ModuleType, ModuleType]:
    """``frozen.py`` and ``_common.py``, loaded as siblings, as they are when run from their folder."""
    sys.path.insert(0, str(BENCHMARKS))
    try:
        spec = importlib.util.spec_from_file_location("frozen_results", BENCHMARKS / "frozen.py")
        assert spec is not None and spec.loader is not None
        frozen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(frozen)
        return frozen, sys.modules["_common"]
    finally:
        sys.path.remove(str(BENCHMARKS))
        for name in SIBLINGS:
            sys.modules.pop(name, None)


def price_mode(mode: str, *, default: str, depth_mark: str) -> str | None:
    """A frozen row's ``mode`` as the price list keys it.

    ``default`` (the scan asked for none) is ``None``; ``@deep`` is the
    ``search_depth`` ``deep``; ``ultra-fast`` is the vendor mode
    ``:ultra-fast``. A vendor mode at a depth (``ultra-fast@deep``) has no row
    and is kept whole, so it is reported rather than matched.
    """
    if mode == default:
        return None
    vendor, _, depth = mode.partition(depth_mark)
    if not vendor:
        return depth
    return ":" + mode


def run_index(manifest: Mapping[str, Any], split_setup: Any) -> dict[tuple[str, str, str], tuple[str, date]]:
    """``(set, provider, mode)`` -> the run that scanned it and the day it started, from ``manifest.json``."""
    index: dict[tuple[str, str, str], tuple[str, date]] = {}
    for set_name, entry in manifest["sets"].items():
        for run in entry["runs"]:
            started = date.fromisoformat(str(run["started"])[:10])
            for label in run["providers"]:
                provider, mode = split_setup(label)
                index[(set_name, provider, mode)] = (run["run"], started)
    return index


def pooled_sets(sets: Mapping[str, Mapping[str, Any]], *, mixed: str) -> list[str]:
    """The manifest's sets ``make latency`` pools, in order.

    A set asking the same questions (``answers_sha256``) by the same method as
    one listed before it is a rerun, such as ``sealqa-hard-254-twin`` on two
    backends: pooling it would count those questions twice for those backends
    only. A ``mixed`` set calls several methods, and copies its answer rows
    from another set.
    """
    seen: set[tuple[str, str]] = set()
    pooled = []
    for name, entry in sets.items():
        questions = (entry["answers_sha256"], entry["method"])
        if entry["method"] == mixed or questions in seen:
            continue
        seen.add(questions)
        pooled.append(name)
    return pooled


def measure(
    rows: Iterable[Mapping[str, Any]],
    runs: Mapping[tuple[str, str, str], tuple[str, date]],
    *,
    pct: Any,
    default: str,
    depth_mark: str,
) -> tuple[dict[Key, dict[str, Any]], set[tuple[str, str, str]]]:
    """Each price row's latency fields, pooled over every frozen row it matches, and the frozen setups none matched."""
    samples: dict[Key, list[float]] = {}
    sources: dict[Key, set[tuple[str, date]]] = {}
    unmatched: set[tuple[str, str, str]] = set()
    for row in rows:
        if row["method"] not in LATENCY_METHODS:
            continue
        mode = price_mode(row["mode"], default=default, depth_mark=depth_mark)
        priced = price(row["provider"], row["method"], mode)
        # Matched before the error filter, so a setup whose every call failed is still reported.
        if priced is None:
            unmatched.add((row["provider"], row["method"], row["mode"]))
            continue
        if row["error"] is not None or row["latency_s"] is None:
            continue
        samples.setdefault(priced.key, []).append(float(row["latency_s"]))
        sources.setdefault(priced.key, set()).add(runs[(row["set"], row["provider"], row["mode"])])
    filled = {
        key: {
            "latency_p50_s": pct(values, P50),
            "latency_p90_s": pct(values, P90),
            "latency_n": len(values),
            "latency_runs": sorted(run for run, _ in sources[key]),
            "latency_measured": max(day for _, day in sources[key]).isoformat(),
        }
        for key, values in samples.items()
    }
    return filled, unmatched


def frozen_latency() -> tuple[dict[Key, dict[str, Any]], set[tuple[str, str, str]]]:
    """The latency fields the pooled frozen sets support, after checking the files against ``SHA256SUMS``."""
    frozen, common = _benchmarks()
    frozen.verify(frozen.FROZEN_DIR)
    manifest = json.loads((frozen.FROZEN_DIR / frozen.MANIFEST_FILE).read_text(encoding="utf-8"))
    manifest = {"sets": {name: manifest["sets"][name] for name in pooled_sets(manifest["sets"], mixed=frozen.MIXED)}}
    rows = [row for name in manifest["sets"] for row in frozen.read_rows(frozen.FROZEN_DIR / (name + frozen.SUFFIX))]
    return measure(
        rows,
        run_index(manifest, frozen.split_setup),
        pct=common._pct,
        default=frozen.DEFAULT_MODE,
        depth_mark=common.DEPTH_MARK,
    )


def regenerate(text: str, filled: Mapping[Key, Mapping[str, Any]]) -> str:
    """``prices.json`` with every row's latency fields replaced: measured where ``filled`` has the row, else null."""
    data = json.loads(text)
    rows = []
    for row in data["rows"]:
        key = (row["provider"], row["method"], row.get("mode"))
        latency = filled.get(key, {})
        rows.append(
            {k: v for k, v in row.items() if k not in LATENCY_FIELDS} | {f: latency.get(f) for f in LATENCY_FIELDS}
        )
    data["rows"] = rows
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if prices.json's latency fields are stale")
    args = parser.parse_args(argv)
    filled, unmatched = frozen_latency()
    for provider, method, mode in sorted(unmatched):
        print(f"no price row for frozen ({provider}, {method}, {mode}): left out", file=sys.stderr)
    current = PRICES_JSON.read_text(encoding="utf-8")
    fresh = regenerate(current, filled)
    if args.check:
        if fresh != current:
            print(MSG_LATENCY_STALE, file=sys.stderr)
            return 1
        return 0
    PRICES_JSON.write_text(fresh, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
