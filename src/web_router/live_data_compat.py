"""The names live data had when it was "templates", kept working with a warning.

``from web_router import TemplateResponse`` still works, and so does importing
it from the module it used to live in (``web_router.types``) or calling a
provider's ``templates()``: each old name warns and resolves to the live data
name that replaced it. Removed in a future minor release; ``CHANGELOG.md`` says
which.
"""

from __future__ import annotations

import importlib
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from web_router.providers.base import LiveDataProvider
    from web_router.types_live_data import LiveDataSource, LiveDataSourcePage

RENAMED: dict[str, tuple[str, str]] = {
    "Template": ("web_router.types_live_data", "LiveDataSource"),
    "TemplatePage": ("web_router.types_live_data", "LiveDataSourcePage"),
    "TemplateRequest": ("web_router.types_live_data", "LiveDataRequest"),
    "TemplateResponse": ("web_router.types_live_data", "LiveDataResponse"),
    "TemplateSample": ("web_router.types_live_data", "LiveDataSample"),
    "TEMPLATE_PARAMETERS": ("web_router.types_live_data", "LIVE_DATA_PARAMETERS"),
    "TemplateProvider": ("web_router.providers.base", "LiveDataProvider"),
    "TemplateNotFoundError": ("web_router.exceptions", "LiveDataSourceNotFoundError"),
    "available_template_providers": ("web_router.registry", "available_live_data_providers"),
    "configured_template_providers": ("web_router.registry", "configured_live_data_providers"),
    "register_template_provider": ("web_router.registry", "register_live_data_provider"),
    "unregister_template_provider": ("web_router.registry", "unregister_live_data_provider"),
    "NimbleTemplateProvider": ("web_router.providers.nimble_live_data", "NimbleLiveDataProvider"),
}
"""Each old public name, and the module and name that replaced it."""

OLD_HOMES: dict[str, frozenset[str]] = {
    "web_router.types": frozenset(
        {"Template", "TemplatePage", "TemplateRequest", "TemplateResponse", "TemplateSample", "TEMPLATE_PARAMETERS"}
    ),
    "web_router.providers.base": frozenset({"TemplateProvider"}),
    "web_router.exceptions": frozenset({"TemplateNotFoundError"}),
    "web_router.registry": frozenset(
        {
            "available_template_providers",
            "configured_template_providers",
            "register_template_provider",
            "unregister_template_provider",
        }
    ),
    "web_router.providers.nimble_template": frozenset({"NimbleTemplateProvider"}),
}
"""The module each old name was importable from before the rename, beside :mod:`web_router` itself."""


def resolve(name: str) -> Any:
    """The object an old name now means, with a :class:`DeprecationWarning`; ``AttributeError`` for any other name."""
    if name not in RENAMED:
        raise AttributeError(f"module 'web_router' has no attribute {name!r}")
    module, new = RENAMED[name]
    _warn(name, new)
    return getattr(importlib.import_module(module), new)


def module_getattr(module: str, fallback: Callable[[str], Any] | None = None) -> Callable[[str], Any]:
    """A module-level ``__getattr__`` for one of :data:`OLD_HOMES`: its own old names, with the warning.

    Any other name goes to ``fallback``, the module's own ``__getattr__`` if it had one.
    """
    names = OLD_HOMES[module]

    def __getattr__(name: str) -> Any:
        if name in names:
            return resolve(name)
        if fallback is not None:
            return fallback(name)
        raise AttributeError(f"module {module!r} has no attribute {name!r}")

    return __getattr__


def install() -> None:
    """Give each old name's former module a ``__getattr__`` that resolves it, so ``from web_router.types import
    TemplateRequest`` keeps working.

    Called once from :mod:`web_router`, which every submodule import runs first, so those modules carry no
    compatibility code of their own. ``web_router.providers.nimble_template`` is a module of its own instead:
    importing it here would pull in the optional Nimble SDK.
    """
    for module in OLD_HOMES.keys() - {"web_router.providers.nimble_template"}:
        target = importlib.import_module(module)
        vars(target)["__getattr__"] = module_getattr(module, getattr(target, "__getattr__", None))


def _warn(old: str, new: str, *, stacklevel: int = 4) -> None:
    # Imported here: this module is imported by ``web_router.types``, which the constants import.
    from web_router.constants_live_data import MSG_LIVE_DATA_DEPRECATED_NAME

    warnings.warn(MSG_LIVE_DATA_DEPRECATED_NAME.format(old=old, new=new), DeprecationWarning, stacklevel=stacklevel)


def deprecated_keyword(owner: str, given: dict[str, Any], old: str, new: str, value: str | None) -> str:
    """``value``, or the one passed under the keyword's old name, with a warning.

    ``TypeError`` for any other keyword, for both names at once, and for neither.
    """
    unknown = sorted(set(given) - {old})
    if unknown:
        raise TypeError(f"{owner}() got an unexpected keyword argument {unknown[0]!r}")
    if old not in given:
        if value is None:
            raise TypeError(f"{owner}() needs {new}=")
        return value
    if value is not None:
        raise TypeError(f"{owner}() takes {new}= or its old name {old}=, not both")
    _warn(f"{old}=", f"{new}=")
    return str(given[old])


def _provider(mixin: object) -> LiveDataProvider:
    return cast("LiveDataProvider", mixin)


class TemplateMethods:
    """A live data provider's methods under their old names, each warning and calling its replacement."""

    def templates(self, *, limit: int | None = None, offset: int | None = None) -> LiveDataSourcePage:
        """Deprecated: :meth:`~web_router.providers.base.LiveDataProvider.live_data_sources`."""
        _warn("templates", "live_data_sources", stacklevel=3)
        return _provider(self).live_data_sources(limit=limit, offset=offset)

    async def atemplates(self, *, limit: int | None = None, offset: int | None = None) -> LiveDataSourcePage:
        """Deprecated: :meth:`~web_router.providers.base.LiveDataProvider.alive_data_sources`."""
        _warn("atemplates", "alive_data_sources", stacklevel=3)
        return await _provider(self).alive_data_sources(limit=limit, offset=offset)

    def template(self, name: str) -> LiveDataSource:
        """Deprecated: :meth:`~web_router.providers.base.LiveDataProvider.live_data_source`."""
        _warn("template", "live_data_source", stacklevel=3)
        return _provider(self).live_data_source(name)

    async def atemplate(self, name: str) -> LiveDataSource:
        """Deprecated: :meth:`~web_router.providers.base.LiveDataProvider.alive_data_source`."""
        _warn("atemplate", "alive_data_source", stacklevel=3)
        return await _provider(self).alive_data_source(name)
