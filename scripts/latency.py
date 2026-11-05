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
import json
import sys
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

from scripts.benchmark_loader import FROZEN_SCRIPT, FROZEN_SIBLINGS, load_script
from web_router.constants_prices import LATENCY_METHODS, MSG_LATENCY_STALE, PRICES_DATA_DIR, PRICES_DATA_FILE
from web_router.prices import price

ROOT = Path(__file__).resolve().parent.parent
PRICES_JSON = ROOT / "src" / "web_router" / PRICES_DATA_DIR / PRICES_DATA_FILE

LATENCY_FIELDS = ("latency_p50_s", "latency_p90_s", "latency_n", "latency_runs", "latency_measured")
P50, P90 = 0.5, 0.9

Key = tuple[str, str, "str | None"]


def _benchmarks() -> tuple[ModuleType, ModuleType]:
    """``frozen.py`` and ``_common.py``, loaded as siblings, as they are when run from their folder."""
    with load_script(FROZEN_SCRIPT, "frozen_for_latency", FROZEN_SIBLINGS) as frozen:
        return frozen, sys.modules["_common"]


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


def pooled_sets(sets: Mapping[str, Mapping[str, Any]], *, mixed: str) -> dict[str, frozenset[str]]:
    """The backend setups ``make latency`` pools from each manifest set, in order.

    A setup asking the same questions (``answers_sha256``) by the same method
    as a set listed before it is a rerun, such as ``sealqa-hard-254-twin`` on
    two backends: pooling it would count those questions twice for that
    backend. A setup the earlier set did not have is new data and is pooled. A
    ``mixed`` set calls several methods and copies its answer rows from
    another set, so it is left out.
    """
    seen: set[tuple[str, str, str]] = set()
    pooled: dict[str, frozenset[str]] = {}
    for name, entry in sets.items():
        if entry["method"] == mixed:
            continue
        questions = (entry["answers_sha256"], entry["method"])
        new = frozenset(p for p in entry["providers"] if (*questions, p) not in seen)
        seen.update((*questions, p) for p in new)
        if new:
            pooled[name] = new
    return pooled


def pooled_rows(
    rows: Mapping[str, Iterable[Mapping[str, Any]]], pooled: Mapping[str, frozenset[str]]
) -> list[Mapping[str, Any]]:
    """Each pooled set's rows from its pooled backends, in every mode: the manifest names backends, not modes."""
    return [row for name, backends in pooled.items() for row in rows[name] if row["provider"] in backends]


def measure(
    rows: Iterable[Mapping[str, Any]],
    runs: Mapping[tuple[str, str, str], tuple[str, date]],
    *,
    pct: Any,
    price_mode: Callable[[str, str], str | None],
) -> tuple[dict[Key, dict[str, Any]], set[tuple[str, str, str]]]:
    """Each price row's latency fields, pooled over every frozen row it matches, and the frozen setups none matched.

    ``price_mode`` is ``frozen.py``'s: a row's method and mode as the price list keys the mode.
    """
    samples: dict[Key, list[float]] = {}
    sources: dict[Key, set[tuple[str, date]]] = {}
    unmatched: set[tuple[str, str, str]] = set()
    for row in rows:
        if row["method"] not in LATENCY_METHODS:
            continue
        mode = price_mode(row["method"], row["mode"])
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
    pooled = pooled_sets(manifest["sets"], mixed=frozen.MIXED)
    manifest = {"sets": {name: manifest["sets"][name] for name in pooled}}
    rows = pooled_rows({name: frozen.read_rows(frozen.FROZEN_DIR / (name + frozen.SUFFIX)) for name in pooled}, pooled)
    return measure(
        rows,
        run_index(manifest, frozen.split_setup),
        pct=common._pct,
        price_mode=frozen.price_mode,
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
