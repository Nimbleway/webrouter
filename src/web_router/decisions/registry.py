"""Decider registry: a name to a provider class, imported lazily, as the provider registry does for backends.

``WebRouter(decider="jev")`` resolves through here, so a registered name works
the same way: ``register_decider("acme", AcmeDecider)`` and then
``WebRouter(decider="acme")``. An instance handed to ``WebRouter`` never comes
through here at all.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from web_router.constants import (
    EXTRA_ALL,
    EXTRA_EVALS,
    EXTRA_SEARCH,
    MSG_ROUTE_UNKNOWN_DECIDER,
    ROUTE_DECIDER_JEV,
    ROUTE_DECIDER_KEYWORDS,
    ROUTE_DECIDER_LLM,
)
from web_router.decisions.base import DecisionProvider
from web_router.exceptions import ConfigurationError, ProviderNotInstalledError

DeciderFactory = Callable[..., DecisionProvider]

# name -> (module path, class name, extra that supplies what it imports)
#
# Jev talks to TypeSafe over httpx, which `search` installs; the llm decider
# needs the openai SDK the `evals` extra already ships. The keyword rules need
# nothing, so their column is never quoted.
_BUILTIN_DECIDERS: dict[str, tuple[str, str, str]] = {
    ROUTE_DECIDER_JEV: ("web_router.decisions.jev", "JevDecider", EXTRA_SEARCH),
    ROUTE_DECIDER_LLM: ("web_router.decisions.llm", "LLMDecider", EXTRA_EVALS),
    ROUTE_DECIDER_KEYWORDS: ("web_router.decisions.keywords", "KeywordsDecider", EXTRA_ALL),
}

_CUSTOM_DECIDERS: dict[str, DeciderFactory] = {}


def register_decider(name: str, factory: DeciderFactory) -> None:
    """Register a decider class, or any callable building one, under ``name``, overriding a builtin of that name."""
    _CUSTOM_DECIDERS[name] = factory


def unregister_decider(name: str) -> None:
    """Remove a registered decider. A no-op if it was never registered."""
    _CUSTOM_DECIDERS.pop(name, None)


def available_deciders() -> list[str]:
    """Every decider name ``WebRouter(decider=...)`` accepts, installed or not."""
    return sorted(set(_BUILTIN_DECIDERS) | set(_CUSTOM_DECIDERS))


def get_decider_factory(name: str) -> DeciderFactory:
    """Resolve a decider name to what builds it, importing a builtin's module on first use.

    Raises:
        ConfigurationError: for a name nothing is registered under.
        ProviderNotInstalledError: for a builtin whose dependency is not installed.
    """
    if name in _CUSTOM_DECIDERS:
        return _CUSTOM_DECIDERS[name]
    if name not in _BUILTIN_DECIDERS:
        available = ", ".join(map(repr, available_deciders()))
        raise ConfigurationError(MSG_ROUTE_UNKNOWN_DECIDER.format(name=name, available=available))
    module_path, class_name, extra = _BUILTIN_DECIDERS[name]
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        # As in web_router.registry: only a missing dependency means "not installed"; our own ImportError is a bug.
        if exc.name and exc.name.startswith("web_router"):
            raise
        raise ProviderNotInstalledError(name, extra) from exc
    factory: DeciderFactory = getattr(module, class_name)
    return factory


def create_decider(name: str, **kwargs: Any) -> DecisionProvider:
    """Build the decider registered under ``name``, passing ``kwargs`` to it: ``WebRouter``'s ``decider_config``."""
    return get_decider_factory(name)(**kwargs)
