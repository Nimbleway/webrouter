"""The routers' template, research, browser and browse provider lookups, kept apart so ``client.py`` stops growing.

:class:`~web_router.WebRouter` and :class:`~web_router.AsyncWebRouter` inherit them through their shared base, so
``router.get_template_provider`` and the rest are where they always were.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

from web_router.providers.base import BrowseProvider, BrowserProvider, ResearchProvider, TemplateProvider
from web_router.registry import (
    create_browse_provider,
    create_browser_provider,
    create_research_provider,
    create_template_provider,
)
from web_router.types import Provider, provider_name

_P = TypeVar("_P")


class ProviderLookup:
    """Each lookup builds a backend once, under the router's lock, from its ``provider_config`` entry."""

    default_provider: str
    _providers_lock: threading.Lock
    _template_instances: dict[str, TemplateProvider]
    _research_instances: dict[str, ResearchProvider]
    _browser_instances: dict[str, BrowserProvider]
    _browse_instances: dict[str, BrowseProvider]

    def _config_for(self, key: str) -> dict[str, Any]:
        raise NotImplementedError

    def _built(self, instances: dict[str, _P], key: str, create: Callable[..., _P]) -> _P:
        """``instances[key]``, built on first use: one lock and one rule for every lookup here."""
        with self._providers_lock:
            if key not in instances:
                instances[key] = create(key, **self._config_for(key))
            return instances[key]

    def get_template_provider(self, name: str | Provider | TemplateProvider) -> TemplateProvider:
        """Return the named template provider, constructing and caching it on first use.

        The template counterpart of :meth:`~web_router.WebRouter.get_provider`, with the same
        ownership rule -- but no default: a template's name belongs to one
        vendor, so the caller always says which.
        """
        if isinstance(name, TemplateProvider):
            return name
        return self._built(self._template_instances, provider_name(name), create_template_provider)

    def get_research_provider(self, name: str | Provider | ResearchProvider | None = None) -> ResearchProvider:
        """Return the named research provider, constructing and caching it on first use.

        The research counterpart of :meth:`~web_router.WebRouter.get_provider`, with the same
        ownership rule.
        """
        if isinstance(name, ResearchProvider):
            return name
        key = provider_name(name) if name is not None else self.default_provider
        return self._built(self._research_instances, key, create_research_provider)

    def get_browser_provider(self, name: str | Provider | BrowserProvider | None = None) -> BrowserProvider:
        """Return the named browser provider, constructing and caching it on first use.

        The browser counterpart of :meth:`~web_router.WebRouter.get_provider`, with the same
        ownership rule.
        """
        if isinstance(name, BrowserProvider):
            return name
        key = provider_name(name) if name is not None else self.default_provider
        return self._built(self._browser_instances, key, create_browser_provider)

    def get_browse_provider(self, name: str | Provider | BrowseProvider | None = None) -> BrowseProvider:
        """Return the named browse provider, constructing and caching it on first use.

        The browse counterpart of :meth:`~web_router.WebRouter.get_provider`, with the same
        ownership rule.
        """
        if isinstance(name, BrowseProvider):
            return name
        key = provider_name(name) if name is not None else self.default_provider
        return self._built(self._browse_instances, key, create_browse_provider)
