"""Every name the package exports imports from it, the registry's re-exported ones included. Offline."""

from __future__ import annotations

import web_router
from web_router import registry, registry_answer


def test_every_exported_name_resolves_once() -> None:
    assert len(web_router.__all__) == len(set(web_router.__all__))
    assert [name for name in web_router.__all__ if not hasattr(web_router, name)] == []


def test_the_registry_names_are_exported() -> None:
    for module in (registry, registry_answer):
        prefixes = ("available_", "configured_", "register_", "unregister_")
        public = {name for name in vars(module) if name.startswith(prefixes)}
        assert public <= set(web_router.__all__), public - set(web_router.__all__)
