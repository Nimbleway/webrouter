"""The benchmark loaders' sibling lists: each names every module its script imports from its folder."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from scripts import latency
from scripts.benchmark_loader import BENCHMARKS, FROZEN_SIBLINGS
from tests.benchmarks._router_latency_fakes import SCRIPT as ROUTER_LATENCY_SCRIPT
from tests.benchmarks._router_latency_fakes import SIBLINGS as ROUTER_LATENCY_SIBLINGS
from tests.benchmarks.conftest import (
    CHAINS_SIBLINGS,
    EFFORTS_SIBLINGS,
    ROUTE_EVAL_SCRIPT,
    ROUTE_EVAL_SIBLINGS,
    SCAN_SCRIPT,
    SCAN_SIBLINGS,
    load_script,
)

FOLDER = ROUTE_EVAL_SCRIPT.parent


def _from_folder() -> set[str]:
    return {name for name, m in list(sys.modules.items()) if Path(getattr(m, "__file__", None) or "/").parent == FOLDER}


@pytest.mark.parametrize(
    ("script", "siblings"),
    [
        (ROUTE_EVAL_SCRIPT, ROUTE_EVAL_SIBLINGS),
        (FOLDER / "chains.py", CHAINS_SIBLINGS),
        (FOLDER / "efforts.py", EFFORTS_SIBLINGS),
        (SCAN_SCRIPT, SCAN_SIBLINGS),
        (ROUTER_LATENCY_SCRIPT, ROUTER_LATENCY_SIBLINGS),
    ],
    ids=["route_eval", "chains", "efforts", "run", "router_latency"],
)
def test_a_script_loaded_for_a_test_leaves_none_of_its_siblings_behind(script: Path, siblings: tuple[str, ...]) -> None:
    """A sibling left loaded would be reused by the next test module: a stale noise floor, say."""
    before = _from_folder()
    with load_script(script, "loader_under_test", siblings):
        pass
    assert _from_folder() - before == set()


def test_loading_frozen_keeps_the_siblings_another_loader_imported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(BENCHMARKS))
    mine = {name: importlib.import_module(name) for name in FROZEN_SIBLINGS}
    for name, module in mine.items():
        monkeypatch.setitem(sys.modules, name, module)
    latency._benchmarks()
    assert all(sys.modules.get(name) is module for name, module in mine.items())


def test_loading_frozen_leaves_none_of_its_siblings_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    """``FROZEN_SIBLINGS`` names every module ``frozen.py`` imports from its folder: none outlives the load."""
    for name in _from_folder():
        monkeypatch.delitem(sys.modules, name)
    latency._benchmarks()
    assert _from_folder() == set(), "add them to FROZEN_SIBLINGS in scripts/benchmark_loader.py"
