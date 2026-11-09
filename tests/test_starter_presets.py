"""The starter presets in ``presets/``: each loads, passes its own checks and reaches the backend as written.

The calls go to the in-memory fakes, so nothing here touches the network or needs a key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeProvider
from tests.presets_helpers import RecordingExtract
from web_router import WebRouter, available_answer_providers, available_extract_providers, available_providers
from web_router.constants_deepseek import DEEPSEEK_QUERY_LIMIT
from web_router.constants_presets import PRESET_FILE_SUFFIXES
from web_router.providers.deepseek import DeepSeekProvider
from web_router.registry import get_extract_provider_class, register_extract_provider
from web_router.types import SearchRequest

STARTER = Path(__file__).resolve().parents[1] / "presets"
FILES = sorted(file for file in STARTER.rglob("*") if file.is_file() and file.suffix in PRESET_FILE_SUFFIXES)
FOLDERS = sorted({file.parent for file in FILES})
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


def test_the_starter_library_is_not_empty() -> None:
    assert FILES


@pytest.mark.parametrize("file", FILES, ids=lambda file: file.stem)
def test_each_starter_preset_loads_and_passes_its_own_checks(file: Path) -> None:
    with WebRouter() as router:
        (preset,) = router.presets.load(file)
        for component in preset.applies_to:
            check = router.presets.validate(component, MINIMAL_CALL[component], preset=preset.ref)
            assert check.ok, [issue.message for issue in check.issues]
            # presets/README.md says a call can override every setting a starter preset fixes.
            settings = preset.components[component]
            assert set(settings.overridable) == set(settings.defaults), component


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda folder: folder.name)
def test_each_starter_folder_loads_as_a_whole(folder: Path) -> None:
    with WebRouter() as router:
        loaded = router.presets.load(folder)

    files_in_folder = [file for file in FILES if file.parent == folder]
    assert len({preset.ref for preset in loaded}) == len(files_in_folder)


@pytest.mark.parametrize("file", FILES, ids=lambda file: file.stem)
def test_each_starter_preset_works_on_the_backends_its_readme_names(file: Path) -> None:
    with WebRouter() as router:
        (preset,) = router.presets.load(file)
        passing = {}
        for component in preset.applies_to:
            named = set(AVAILABLE[component]()) - ROUTERS - REFUSED_AT_CALL_TIME.get(component, set())
            call = MINIMAL_CALL[component]
            passing[component] = {
                name
                for name in named
                if router.presets.validate(component, {**call, "provider": name}, preset=preset.ref).ok
            }

    assert passing == {component: SERVED_BY[(preset.id, component)] for component in preset.applies_to}


@pytest.mark.parametrize("file", FILES, ids=lambda file: file.stem)
def test_deepseek_cannot_take_a_starter_search_presets_domain_lists(file: Path) -> None:
    with WebRouter() as router:
        (preset,) = router.presets.load(file)
    if "search" not in preset.applies_to:
        pytest.skip("no search component")
    defaults = preset.components["search"].defaults
    assert len(DeepSeekProvider._query(SearchRequest(query="q", **defaults))) > DEEPSEEK_QUERY_LIMIT


def test_a_calls_empty_domain_list_replaces_the_presets_and_none_keeps_it(fake_provider: Any) -> None:
    router = WebRouter("fake")
    (preset,) = router.presets.load(STARTER / "ecommerce" / "coupons.json")

    router.search("q", preset=preset.ref, include_domains=[])
    router.search("q", preset=preset.ref, include_domains=None)

    provider = router.get_provider("fake")
    assert isinstance(provider, FakeProvider)
    emptied, kept = provider.requests
    assert emptied.include_domains == []
    assert kept.include_domains == preset.components["search"].defaults["include_domains"]


@pytest.mark.parametrize("file", FILES, ids=lambda file: file.stem)
def test_each_starter_preset_reaches_the_backend_as_written(
    file: Path, fake_provider: Any, fake_answer_provider: Any, fake_extract_provider: Any
) -> None:
    register_extract_provider("fake", RecordingExtract)  # the fixture's teardown unregisters it
    router = WebRouter("fake")
    (preset,) = router.presets.load(file)
    backends = {
        "search": router.get_provider,
        "answer": router.get_answer_provider,
        "extract": router.get_extract_provider,
    }

    for component in preset.applies_to:
        call = MINIMAL_CALL[component]
        getattr(router, component)(*call.values(), preset=preset.ref)
        sent = backends[component]("fake").requests[-1].model_dump()  # type: ignore[attr-defined]
        defaults = preset.components[component].defaults
        assert {key: sent[key] for key in defaults} == defaults, component
