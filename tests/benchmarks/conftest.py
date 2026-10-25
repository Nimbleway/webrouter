"""What the benchmark scans' tests share: the extract scan loaded from its folder, and a measured row to score."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

EXTRACT_SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "extract.py"
EXTRACT_SIBLINGS = ("_common", "cli", "extract_report", "urls")
"""What the extract scan imports from its folder, under plain names, so they are forgotten once it is."""
EXTRACT_SETUP = {
    "urls": "general",
    "urls_title": "the general mix",
    "urls_sha256": "abc",
    "url_column": "url",
    "category_column": "category",
    "limit": 12,
    "seed": 0,
    "format": None,
    "timeout": 120.0,
    "started": "2026-10-13T00:00:00+00:00",
}
"""A ``run.json`` as ``setup_of()`` writes it, minus the backends, which each test names."""


@pytest.fixture(scope="module")
def extract_scan() -> Iterator[ModuleType]:
    # The scan imports what it shares with its neighbour as a sibling, as it does run from its folder.
    sys.path.insert(0, str(EXTRACT_SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("extract_scan", EXTRACT_SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(EXTRACT_SCRIPT.parent))
        for name in EXTRACT_SIBLINGS:
            sys.modules.pop(name, None)


def extract_row(
    provider: str,
    url: str,
    chars: int | None,
    category: str = "News / Media",
    error_type: str | None = "ProviderError",
) -> dict[str, Any]:
    """A measured row: ``chars`` is the text that came back, ``None`` a call that did not answer."""
    returned = chars is not None
    return {
        "url": url,
        "category": category,
        "provider": provider,
        "returned": returned,
        "with_text": bool(chars),
        "chars": chars or 0,
        "format": "markdown" if returned else None,
        "status_code": 200 if returned else None,
        "latency": 1.0,
        "error": None if returned else "HTTP 500",
        "error_type": None if returned else error_type,
    }
