"""Which backend serves a router's call: ``provider_config`` per backend, and one cached instance per name.

Kept apart so ``client.py`` stops growing. Both routers take :class:`ProviderLookup`
as a mixin, so ``router.get_provider`` and the other getters are where they always
were; ``get_answer_provider`` is in :mod:`web_router.client_answer` and
``get_template_provider`` in :mod:`web_router.client_template`.
"""

from __future__ import annotations

import threading
from typing import Any

from web_router.constants import DELEGATING_PROVIDERS, MAX_INHERITS_FROM_AUTO, PROVIDER_AUTO, PROVIDER_MAX
from web_router.providers.base import (
    BrowseProvider,
    BrowserProvider,
    ExtractProvider,
    ResearchProvider,
    SearchProvider,
)
from web_router.registry import (
    create_browse_provider,
    create_browser_provider,
    create_extract_provider,
    create_provider,
    create_research_provider,
)
from web_router.types import Provider, provider_name


class ProviderLookup:
    """The getters both routers share, each constructing and caching its backend on first use."""

    default_provider: str
    _config: dict[str, dict[str, Any]]
    _providers_lock: threading.Lock
    _instances: dict[str, SearchProvider]
    _extract_instances: dict[str, ExtractProvider]
    _research_instances: dict[str, ResearchProvider]
    _browser_instances: dict[str, BrowserProvider]
    _browse_instances: dict[str, BrowseProvider]

    def _config_for(self, key: str) -> dict[str, Any]:
        """Constructor kwargs for one backend: its ``provider_config`` entry, and every entry for a delegating one.

        Each of those builds its own members, so it is handed the config they
        would get by name -- a key passed in code reaches Nimble the same way
        through ``auto`` or ``max`` as through ``provider="nimble"``. ``max``
        races the members ``auto`` would try, so a ``chain`` or
        ``members_config`` set under ``auto`` is its own unless it sets one.
        """
        config = dict(self._config.get(key, {}))
        if key == PROVIDER_MAX:
            shared = self._config.get(PROVIDER_AUTO, {})
            for entry in MAX_INHERITS_FROM_AUTO:
                if entry in shared and entry not in config:
                    config[entry] = shared[entry]
        if key in DELEGATING_PROVIDERS:
            siblings: dict[str, dict[str, Any]] = {name: c for name, c in self._config.items() if name != key}
            # An explicit members_config is laid over the sibling entries, member by member, never in place of them.
            explicit: dict[str, dict[str, Any]] = {
                provider_name(name): dict(c) for name, c in (config.get("members_config") or {}).items()
            }
            config["members_config"] = {
                name: {**siblings.get(name, {}), **explicit.get(name, {})} for name in {*siblings, *explicit}
            }
        return config

    def get_provider(self, name: str | Provider | SearchProvider | None = None) -> SearchProvider:
        """Return the named search provider, constructing and caching it on first use.

        An already-constructed provider is returned untouched and never cached,
        so this router does not take ownership of something it did not build --
        see :meth:`WebRouter.close`.
        """
        if isinstance(name, SearchProvider):
            return name

        key = provider_name(name) if name is not None else self.default_provider
        with self._providers_lock:
            if key not in self._instances:
                self._instances[key] = create_provider(key, **self._config_for(key))
            return self._instances[key]

    def get_extract_provider(self, name: str | Provider | ExtractProvider | None = None) -> ExtractProvider:
        """Return the named extract provider, constructing and caching it on first use.

        The extract counterpart of :meth:`get_provider`, with the same
        ownership rule.
        """
        if isinstance(name, ExtractProvider):
            return name

        key = provider_name(name) if name is not None else self.default_provider
        with self._providers_lock:
            if key not in self._extract_instances:
                self._extract_instances[key] = create_extract_provider(key, **self._config_for(key))
            return self._extract_instances[key]

    def get_research_provider(self, name: str | Provider | ResearchProvider | None = None) -> ResearchProvider:
        """Return the named research provider, constructing and caching it on first use.

        The research counterpart of :meth:`get_provider`, with the same
        ownership rule.
        """
        if isinstance(name, ResearchProvider):
            return name

        key = provider_name(name) if name is not None else self.default_provider
        with self._providers_lock:
            if key not in self._research_instances:
                self._research_instances[key] = create_research_provider(key, **self._config_for(key))
            return self._research_instances[key]

    def get_browser_provider(self, name: str | Provider | BrowserProvider | None = None) -> BrowserProvider:
        """Return the named browser provider, constructing and caching it on first use.

        The browser counterpart of :meth:`get_provider`, with the same
        ownership rule.
        """
        if isinstance(name, BrowserProvider):
            return name

        key = provider_name(name) if name is not None else self.default_provider
        with self._providers_lock:
            if key not in self._browser_instances:
                self._browser_instances[key] = create_browser_provider(key, **self._config_for(key))
            return self._browser_instances[key]

    def get_browse_provider(self, name: str | Provider | BrowseProvider | None = None) -> BrowseProvider:
        """Return the named browse provider, constructing and caching it on first use.

        The browse counterpart of :meth:`get_provider`, with the same
        ownership rule.
        """
        if isinstance(name, BrowseProvider):
            return name

        key = provider_name(name) if name is not None else self.default_provider
        with self._providers_lock:
            if key not in self._browse_instances:
                self._browse_instances[key] = create_browse_provider(key, **self._config_for(key))
            return self._browse_instances[key]
