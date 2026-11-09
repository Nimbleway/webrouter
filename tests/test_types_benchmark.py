"""The benchmark models moved to ``types_benchmark``; the old import paths still work."""

from __future__ import annotations

import pytest

import web_router
import web_router.types as types
import web_router.types_benchmark as types_benchmark


@pytest.mark.parametrize("name", ["BenchmarkRun", "BenchmarkReport", "ExtractBenchmarkRun", "ExtractBenchmarkReport"])
def test_a_benchmark_model_imports_from_every_path_it_ever_had(name: str) -> None:
    moved = getattr(types_benchmark, name)

    assert getattr(types, name) is moved
    assert getattr(web_router, name) is moved


def test_from_web_router_types_import_still_works() -> None:
    from web_router.types import BenchmarkRun

    assert BenchmarkRun is types_benchmark.BenchmarkRun


def test_an_unknown_name_still_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'NotAModel'"):
        _ = types.NotAModel
