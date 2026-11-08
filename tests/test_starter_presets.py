"""The starter presets in ``presets/``: each loads, passes its own checks and reaches the backend as written.

The calls go to the in-memory fakes, so nothing here touches the network or needs a key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from web_router import WebRouter, available_answer_providers, available_extract_providers, available_providers
from web_router.constants_presets import PRESET_FILE_SUFFIXES
from web_router.presets import PresetRegistry
from web_router.registry import get_extract_provider_class

STARTER = Path(__file__).resolve().parents[1] / "presets"
FILES = sorted(file for file in STARTER.rglob("*") if file.is_file() and file.suffix in PRESET_FILE_SUFFIXES)
FOLDERS = sorted({file.parent for file in FILES})
COMPONENTS = [(file, component) for file in FILES for component in PresetRegistry().load(file)[0].applies_to]
MINIMAL_CALL = {
    "search": {"query": "q"},
    "answer": {"query": "q"},
    "extract": {"urls": ["https://example.com"]},
}
AVAILABLE = {
    "search": available_providers,
    "answer": available_answer_providers,
    "extract": available_extract_providers,
}
# `auto` and `max` route to the backends a router holds keys for, which a check of the class alone cannot see.
ROUTERS = {"auto", "max"}
# DeepSeek maps the domain lists, but as site: operators that run past its query limit, refused only at call time.
REFUSED_AT_CALL_TIME = {"search": {"deepseek"}}
# The backends each preset component works on, as presets/README.md documents them.
SERVED_BY = {
    ("ecommerce.coupons", "search"): {"agentcore", "linkup", "nimble"},
    ("ecommerce.product-by-gtin", "search"): {"agentcore", "linkup", "nimble"},
    ("ecommerce.product-by-name", "search"): {"agentcore", "linkup", "nimble"},
    ("ecommerce.product-similarity", "answer"): {"linkup", "perplexity", "you"},
    ("ecommerce.shipping-info", "answer"): {"linkup", "perplexity", "you"},
    ("ecommerce.shipping-info", "extract"): {
        name
        for name in set(available_extract_providers()) - ROUTERS
        if "markdown" in get_extract_provider_class(name).supported_formats
    },
}


def _id(item: Any) -> str:
    return item.stem if isinstance(item, Path) else str(item)


def test_the_starter_library_is_not_empty() -> None:
    assert FILES


@pytest.mark.parametrize("file", FILES, ids=_id)
def test_each_starter_preset_loads_and_passes_its_own_checks(file: Path) -> None:
    with WebRouter() as router:
        (preset,) = router.presets.load(file)
        for component in preset.applies_to:
            check = router.presets.validate(component, MINIMAL_CALL[component], preset=preset.ref)
            assert check.ok, [issue.message for issue in check.issues]


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda folder: folder.name)
def test_each_starter_folder_loads_as_a_whole(folder: Path) -> None:
    with WebRouter() as router:
        loaded = router.presets.load(folder)

    files_in_folder = [file for file in FILES if file.parent == folder]
    assert len({preset.ref for preset in loaded}) == len(files_in_folder)


@pytest.mark.parametrize(("file", "component"), COMPONENTS, ids=_id)
def test_each_starter_preset_works_on_the_backends_its_readme_names(file: Path, component: str) -> None:
    with WebRouter() as router:
        (preset,) = router.presets.load(file)
        named = set(AVAILABLE[component]()) - ROUTERS - REFUSED_AT_CALL_TIME.get(component, set())
        passing = {
            name
            for name in named
            if router.presets.validate(component, {**MINIMAL_CALL[component], "provider": name}, preset=preset.ref).ok
        }

    assert passing == SERVED_BY[(preset.id, component)]


@pytest.mark.parametrize(("file", "component"), COMPONENTS, ids=_id)
def test_each_starter_preset_reaches_the_backend_as_written(
    file: Path, component: str, fake_provider: Any, fake_answer_provider: Any, fake_extract_provider: Any
) -> None:
    router = WebRouter("fake")
    (preset,) = router.presets.load(file)
    defaults = preset.components[component].defaults

    if component == "extract":
        response = router.extract(MINIMAL_CALL["extract"]["urls"], preset=preset.ref)
        # The fake extract backend keeps no request; the format it labels its documents with is the one it was asked.
        sent = {"format": response.documents[0].format}
    else:
        getattr(router, component)("q", preset=preset.ref)
        backend = router.get_provider("fake") if component == "search" else router.get_answer_provider("fake")
        sent = backend.requests[0].model_dump()

    assert {key: sent[key] for key in defaults} == defaults
