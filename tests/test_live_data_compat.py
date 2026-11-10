"""The names live data had as "templates" keep working, with a warning, wherever they used to be reachable."""

from __future__ import annotations

import importlib

import pytest

from tests._live_data_fake import FakeLiveDataProvider
from web_router.exceptions import LiveDataSourceNotFoundError
from web_router.live_data_compat import OLD_HOMES, RENAMED


@pytest.mark.parametrize(
    ("module", "name"),
    sorted(
        (module, name)
        for module, names in OLD_HOMES.items()
        if module != "web_router.providers.nimble_template"
        for name in names
    ),
)
def test_each_old_name_imports_from_its_old_module(module: str, name: str) -> None:
    new_module, new_name = RENAMED[name]
    with pytest.warns(DeprecationWarning, match=name):
        old = getattr(importlib.import_module(module), name)
    assert old is getattr(importlib.import_module(new_module), new_name)


def test_the_old_nimble_module_still_names_the_provider() -> None:
    pytest.importorskip("nimble_python")
    from web_router.providers.nimble_live_data import NimbleLiveDataProvider

    with pytest.warns(DeprecationWarning, match="NimbleTemplateProvider"):
        from web_router.providers.nimble_template import NimbleTemplateProvider
    assert NimbleTemplateProvider is NimbleLiveDataProvider


def test_an_old_home_still_refuses_a_name_it_never_had() -> None:
    import web_router.registry

    with pytest.raises(AttributeError, match="no_such_name"):
        web_router.registry.no_such_name  # noqa: B018
    from web_router.types import BenchmarkRun  # the module's own __getattr__ still answers

    assert BenchmarkRun.__name__ == "BenchmarkRun"


def test_a_provider_answers_to_its_old_method_names() -> None:
    provider = FakeLiveDataProvider()
    with pytest.warns(DeprecationWarning, match="live_data_sources"):
        page = provider.templates()
    assert page.names == provider.live_data_sources().names
    with pytest.warns(DeprecationWarning, match="live_data_source"):
        assert provider.template(page.names[0]).name == page.names[0]


async def test_a_provider_answers_to_its_old_async_method_names() -> None:
    provider = FakeLiveDataProvider()
    with pytest.warns(DeprecationWarning, match="alive_data_sources"):
        page = await provider.atemplates()
    with pytest.warns(DeprecationWarning, match="alive_data_source"):
        assert (await provider.atemplate(page.names[0])).name == page.names[0]


def test_the_not_found_error_takes_its_old_keyword() -> None:
    with pytest.warns(DeprecationWarning, match="template="):
        error = LiveDataSourceNotFoundError("nimble", template="books_pdp")
    assert error.source == error.template == "books_pdp"
    assert LiveDataSourceNotFoundError("nimble", "books_pdp").source == "books_pdp"
    with pytest.raises(TypeError, match="source="):
        LiveDataSourceNotFoundError("nimble")
    with pytest.raises(TypeError, match="sauce"):
        LiveDataSourceNotFoundError("nimble", sauce="x")
