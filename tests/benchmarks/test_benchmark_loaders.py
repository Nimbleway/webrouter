"""The benchmark loaders' sibling lists: each names every module its script imports from its folder."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
    ],
    ids=["route_eval", "chains", "efforts", "run"],
)
def test_a_script_loaded_for_a_test_leaves_none_of_its_siblings_behind(script: Path, siblings: tuple[str, ...]) -> None:
    """A sibling left loaded would be reused by the next test module: a stale noise floor, say."""
    before = _from_folder()
    with load_script(script, "loader_under_test", siblings):
        pass
    assert _from_folder() - before == set()
