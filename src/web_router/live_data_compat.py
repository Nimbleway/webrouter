"""The names live data had when it was "templates", kept importable from :mod:`web_router` with a warning.

``from web_router import TemplateResponse`` still works, and warns once per
name: each old name resolves to the live data name that replaced it. Removed in
a future minor release; ``CHANGELOG.md`` says which.
"""

from __future__ import annotations

import importlib
import warnings
from typing import Any

from web_router.constants_live_data import MSG_LIVE_DATA_DEPRECATED_NAME

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
}
"""Each old public name, and the module and name that replaced it."""


def resolve(name: str) -> Any:
    """The object an old name now means, with a :class:`DeprecationWarning`; ``AttributeError`` for any other name."""
    if name not in RENAMED:
        raise AttributeError(f"module 'web_router' has no attribute {name!r}")
    module, new = RENAMED[name]
    warnings.warn(MSG_LIVE_DATA_DEPRECATED_NAME.format(old=name, new=new), DeprecationWarning, stacklevel=3)
    return getattr(importlib.import_module(module), new)
