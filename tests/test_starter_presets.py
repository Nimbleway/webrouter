"""The starter presets in ``presets/``: each one loads, passes its own checks and reaches the backend as written.

Runs over the in-memory fakes, so nothing here touches the network or needs a key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeProvider
from web_router import WebRouter

STARTER = Path(__file__).resolve().parents[1] / "presets"
FILES = sorted(STARTER.rglob("*.json"))
MINIMAL_CALL = {
    "search": {"query": "q"},
    "answer": {"query": "q"},
    "extract": {"urls": ["https://example.com"]},
}


def test_the_starter_library_is_not_empty() -> None:
    assert FILES


@pytest.mark.parametrize("file", FILES, ids=lambda file: file.stem)
def test_each_starter_preset_loads_and_passes_its_own_checks(file: Path) -> None:
    with WebRouter() as router:
        (preset,) = router.presets.load(file)
        for component in preset.applies_to:
            check = router.presets.validate(component, MINIMAL_CALL[component], preset=preset.ref)
            assert check.ok, [issue.message for issue in check.issues]


def test_a_starter_search_preset_reaches_the_backend_as_written(fake_provider: Any) -> None:
    file = STARTER / "ecommerce" / "product-by-gtin.json"
    defaults = json.loads(file.read_text())["components"]["search"]["defaults"]
    router = WebRouter("fake")
    router.presets.load(file)

    router.search("UPC 049000028911", preset="ecommerce.product-by-gtin@1.0.0")

    provider = router.get_provider("fake")
    assert isinstance(provider, FakeProvider)
    request = provider.requests[0]
    assert (request.max_results, request.include_domains) == (defaults["max_results"], defaults["include_domains"])
