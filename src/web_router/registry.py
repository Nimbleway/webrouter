"""Provider registry, one table per method.

Providers are looked up by name and imported lazily, so installing web-router without a provider's optional
dependency stays valid until that provider is actually requested.

There is a table per method rather than per vendor, because a name does not imply a capability: ``brave``
searches and cannot extract, ``oxylabs`` extracts and cannot search, and ``nimble`` searches, extracts, runs
live data and researches through four different classes. Asking the wrong table for a name reports it as unknown
and lists the ones that method does serve, which is the honest answer.
"""

from __future__ import annotations

import importlib
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar

from web_router.constants import (
    AUTO_EXTRA,
    EXTRA_AWS,
    EXTRA_EXTRACT,
    EXTRA_RESEARCH,
    EXTRA_SEARCH,
    PROVIDER_AGENTCORE,
    PROVIDER_AUTO,
    PROVIDER_BRAVE,
    PROVIDER_BRIGHTDATA,
    PROVIDER_BROWSERBASE,
    PROVIDER_DEEPSEEK,
    PROVIDER_EXA,
    PROVIDER_EXA_MCP,
    PROVIDER_FIRECRAWL,
    PROVIDER_JINA,
    PROVIDER_KEENABLE,
    PROVIDER_KEIROLABS,
    PROVIDER_LINKUP,
    PROVIDER_LITEPARSE,
    PROVIDER_LLAMAPARSE,
    PROVIDER_MAX,
    PROVIDER_NIMBLE,
    PROVIDER_OCTEN,
    PROVIDER_OXYLABS,
    PROVIDER_PARALLEL,
    PROVIDER_PARALLEL_MCP,
    PROVIDER_PERPLEXITY,
    PROVIDER_SELTZ,
    PROVIDER_SPECSTREAM,
    PROVIDER_TAVILY,
    PROVIDER_VALYU,
    PROVIDER_WEBIQ,
    PROVIDER_YOU,
    PROVIDER_YOU_MCP,
)
from web_router.constants_browse import BUILTIN_BROWSE_PROVIDERS, BUILTIN_BROWSER_PROVIDERS
from web_router.constants_litellm import PROVIDER_EXA_LITELLM, PROVIDER_PARALLEL_LITELLM, PROVIDER_TAVILY_LITELLM
from web_router.constants_live_data import BUILTIN_LIVE_DATA_PROVIDERS
from web_router.exceptions import ProviderNotFoundError, ProviderNotInstalledError
from web_router.providers._pinning import pinned_class
from web_router.registry_configured import configured_browse_providers as configured_browse_providers
from web_router.registry_configured import configured_browser_providers as configured_browser_providers
from web_router.registry_configured import configured_extract_providers as configured_extract_providers
from web_router.registry_configured import configured_live_data_providers as configured_live_data_providers
from web_router.registry_configured import configured_providers as configured_providers
from web_router.registry_configured import configured_research_providers as configured_research_providers
from web_router.types import Provider, provider_name

if TYPE_CHECKING:
    from web_router.providers.base import (
        BrowseProvider,
        BrowserProvider,
        ExtractProvider,
        LiveDataProvider,
        ResearchProvider,
        SearchProvider,
        _BaseProvider,
    )

_ProviderT = TypeVar("_ProviderT", bound="_BaseProvider")

# name -> (module path, class name, pip extra that supplies its SDK). Extras are per method, so every backend in
# a table points at the same one; the column stays because it is what ProviderNotInstalledError quotes back.
_BUILTIN_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_NIMBLE: ("web_router.providers.nimble", "NimbleProvider", EXTRA_SEARCH),
    PROVIDER_TAVILY: ("web_router.providers.tavily", "TavilyProvider", EXTRA_SEARCH),
    PROVIDER_YOU: ("web_router.providers.you", "YouProvider", EXTRA_SEARCH),
    PROVIDER_BRAVE: ("web_router.providers.brave", "BraveProvider", EXTRA_SEARCH),
    PROVIDER_EXA: ("web_router.providers.exa", "ExaProvider", EXTRA_SEARCH),
    PROVIDER_PARALLEL: ("web_router.providers.parallel_search", "ParallelProvider", EXTRA_SEARCH),
    PROVIDER_PERPLEXITY: ("web_router.providers.perplexity", "PerplexityProvider", EXTRA_SEARCH),
    PROVIDER_LINKUP: ("web_router.providers.linkup", "LinkupProvider", EXTRA_SEARCH),
    PROVIDER_SELTZ: ("web_router.providers.seltz", "SeltzProvider", EXTRA_SEARCH),
    PROVIDER_WEBIQ: ("web_router.providers.webiq", "WebIQProvider", EXTRA_SEARCH),
    PROVIDER_OCTEN: ("web_router.providers.octen", "OctenProvider", EXTRA_SEARCH),
    PROVIDER_KEIROLABS: ("web_router.providers.keirolabs", "KeirolabsProvider", EXTRA_SEARCH),
    PROVIDER_KEENABLE: ("web_router.providers.keenable", "KeenableProvider", EXTRA_SEARCH),
    PROVIDER_DEEPSEEK: ("web_router.providers.deepseek", "DeepSeekProvider", EXTRA_SEARCH),
    PROVIDER_VALYU: ("web_router.providers.valyu", "ValyuProvider", EXTRA_SEARCH),
    PROVIDER_SPECSTREAM: ("web_router.providers.specstream", "SpecStreamProvider", EXTRA_SEARCH),
    PROVIDER_AGENTCORE: ("web_router.providers.agentcore", "AgentCoreProvider", EXTRA_AWS),
    PROVIDER_YOU_MCP: ("web_router.providers.you_mcp", "YouMcpProvider", EXTRA_SEARCH),
    PROVIDER_EXA_MCP: ("web_router.providers.exa_mcp", "ExaMcpProvider", EXTRA_SEARCH),
    PROVIDER_PARALLEL_MCP: ("web_router.providers.parallel_mcp", "ParallelMcpProvider", EXTRA_SEARCH),
    PROVIDER_EXA_LITELLM: ("web_router.providers.litellm", "ExaLiteLLMProvider", EXTRA_SEARCH),
    PROVIDER_TAVILY_LITELLM: ("web_router.providers.litellm", "TavilyLiteLLMProvider", EXTRA_SEARCH),
    PROVIDER_PARALLEL_LITELLM: ("web_router.providers.litellm", "ParallelLiteLLMProvider", EXTRA_SEARCH),
    PROVIDER_AUTO: ("web_router.providers.auto", "AutoProvider", AUTO_EXTRA),
}

_BUILTIN_EXTRACT_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_NIMBLE: ("web_router.providers.nimble", "NimbleExtractProvider", EXTRA_EXTRACT),
    PROVIDER_TAVILY: ("web_router.providers.tavily_extract", "TavilyExtractProvider", EXTRA_EXTRACT),
    PROVIDER_EXA: ("web_router.providers.exa", "ExaExtractProvider", EXTRA_EXTRACT),
    PROVIDER_LINKUP: ("web_router.providers.linkup", "LinkupExtractProvider", EXTRA_EXTRACT),
    PROVIDER_FIRECRAWL: ("web_router.providers.firecrawl", "FirecrawlExtractProvider", EXTRA_EXTRACT),
    PROVIDER_OXYLABS: ("web_router.providers.oxylabs", "OxylabsExtractProvider", EXTRA_EXTRACT),
    PROVIDER_BRIGHTDATA: ("web_router.providers.brightdata", "BrightDataExtractProvider", EXTRA_EXTRACT),
    PROVIDER_WEBIQ: ("web_router.providers.webiq", "WebIQExtractProvider", EXTRA_EXTRACT),
    PROVIDER_KEENABLE: ("web_router.providers.keenable_extract", "KeenableExtractProvider", EXTRA_EXTRACT),
    PROVIDER_DEEPSEEK: ("web_router.providers.deepseek", "DeepSeekExtractProvider", EXTRA_EXTRACT),
    PROVIDER_SPECSTREAM: ("web_router.providers.specstream", "SpecStreamExtractProvider", EXTRA_EXTRACT),
    PROVIDER_JINA: ("web_router.providers.jina", "JinaExtractProvider", EXTRA_EXTRACT),
    PROVIDER_LITEPARSE: ("web_router.providers.liteparse", "LiteParseExtractProvider", EXTRA_EXTRACT),
    PROVIDER_LLAMAPARSE: ("web_router.providers.llamaparse", "LlamaParseExtractProvider", EXTRA_EXTRACT),
    PROVIDER_BROWSERBASE: ("web_router.providers.browserbase_extract", "BrowserbaseExtractProvider", EXTRA_EXTRACT),
    PROVIDER_AUTO: ("web_router.providers.auto", "AutoExtractProvider", AUTO_EXTRA),
    PROVIDER_MAX: ("web_router.providers.max", "MaxExtractProvider", AUTO_EXTRA),
}

# A template run is the Extract API's, so it ships with the extract extra. No `auto`: a template is one vendor's.
_BUILTIN_LIVE_DATA_PROVIDERS = BUILTIN_LIVE_DATA_PROVIDERS

_BUILTIN_RESEARCH_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_NIMBLE: ("web_router.providers.nimble", "NimbleResearchProvider", EXTRA_RESEARCH),
    PROVIDER_PARALLEL: ("web_router.providers.parallel", "ParallelResearchProvider", EXTRA_RESEARCH),
    PROVIDER_PERPLEXITY: ("web_router.providers.perplexity", "PerplexityResearchProvider", EXTRA_RESEARCH),
    PROVIDER_LINKUP: ("web_router.providers.linkup", "LinkupResearchProvider", EXTRA_RESEARCH),
    PROVIDER_YOU: ("web_router.providers.you_research", "YouResearchProvider", EXTRA_RESEARCH),
    PROVIDER_AUTO: ("web_router.providers.auto", "AutoResearchProvider", AUTO_EXTRA),
}

_BUILTIN_BROWSER_PROVIDERS = BUILTIN_BROWSER_PROVIDERS

_BUILTIN_BROWSE_PROVIDERS = BUILTIN_BROWSE_PROVIDERS

_CUSTOM_PROVIDERS: dict[str, type[SearchProvider]] = {}
_CUSTOM_EXTRACT_PROVIDERS: dict[str, type[ExtractProvider]] = {}
_CUSTOM_LIVE_DATA_PROVIDERS: dict[str, type[LiveDataProvider]] = {}
_CUSTOM_RESEARCH_PROVIDERS: dict[str, type[ResearchProvider]] = {}
_CUSTOM_BROWSER_PROVIDERS: dict[str, type[BrowserProvider]] = {}
_CUSTOM_BROWSE_PROVIDERS: dict[str, type[BrowseProvider]] = {}


def is_custom(method: str, name: str) -> bool:
    """Whether ``name`` currently has a caller-registered implementation for ``method``."""
    if method == "answer":
        from web_router.registry_answer import is_custom as answer_custom

        return answer_custom(name)
    tables: dict[str, Any] = {
        "search": _CUSTOM_PROVIDERS,
        "extract": _CUSTOM_EXTRACT_PROVIDERS,
        "live_data": _CUSTOM_LIVE_DATA_PROVIDERS,
        "research": _CUSTOM_RESEARCH_PROVIDERS,
        "browse": _CUSTOM_BROWSE_PROVIDERS,
        "open_browser": _CUSTOM_BROWSER_PROVIDERS,
    }
    return name in tables.get(method, {})


def register_provider(name: str | Provider, provider_cls: type[SearchProvider]) -> None:
    """Register a search provider class under ``name``, overriding any builtin of that name."""
    _CUSTOM_PROVIDERS[provider_name(name)] = provider_cls


def unregister_provider(name: str | Provider) -> None:
    """Remove a previously registered custom search provider. A no-op if it was never registered."""
    _CUSTOM_PROVIDERS.pop(provider_name(name), None)


def register_extract_provider(name: str | Provider, provider_cls: type[ExtractProvider]) -> None:
    """Register an extract provider class under ``name``, overriding any builtin of that name."""
    _CUSTOM_EXTRACT_PROVIDERS[provider_name(name)] = provider_cls


def unregister_extract_provider(name: str | Provider) -> None:
    """Remove a previously registered custom extract provider. A no-op if it was never registered."""
    _CUSTOM_EXTRACT_PROVIDERS.pop(provider_name(name), None)


def register_live_data_provider(name: str | Provider, provider_cls: type[LiveDataProvider]) -> None:
    """Register an Extract Template provider class under ``name``, overriding any builtin of that name."""
    _CUSTOM_LIVE_DATA_PROVIDERS[provider_name(name)] = provider_cls


def unregister_live_data_provider(name: str | Provider) -> None:
    """Remove a previously registered custom template provider. A no-op if it was never registered."""
    _CUSTOM_LIVE_DATA_PROVIDERS.pop(provider_name(name), None)


def register_research_provider(name: str | Provider, provider_cls: type[ResearchProvider]) -> None:
    """Register a research provider class under ``name``, overriding any builtin of that name."""
    _CUSTOM_RESEARCH_PROVIDERS[provider_name(name)] = provider_cls


def unregister_research_provider(name: str | Provider) -> None:
    """Remove a previously registered custom research provider. A no-op if it was never registered."""
    _CUSTOM_RESEARCH_PROVIDERS.pop(provider_name(name), None)


def register_browser_provider(name: str | Provider, provider_cls: type[BrowserProvider]) -> None:
    """Register a browser provider class under ``name``, overriding any builtin of that name."""
    _CUSTOM_BROWSER_PROVIDERS[provider_name(name)] = provider_cls


def unregister_browser_provider(name: str | Provider) -> None:
    """Remove a previously registered custom browser provider. A no-op if it was never registered."""
    _CUSTOM_BROWSER_PROVIDERS.pop(provider_name(name), None)


def register_browse_provider(name: str | Provider, provider_cls: type[BrowseProvider]) -> None:
    """Register a browse provider class under ``name``, overriding any builtin of that name."""
    _CUSTOM_BROWSE_PROVIDERS[provider_name(name)] = provider_cls


def unregister_browse_provider(name: str | Provider) -> None:
    """Remove a previously registered custom browse provider. A no-op if it was never registered."""
    _CUSTOM_BROWSE_PROVIDERS.pop(provider_name(name), None)


def available_providers() -> list[str]:
    """Every search provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_PROVIDERS) | set(_CUSTOM_PROVIDERS))


def available_extract_providers() -> list[str]:
    """Every extract provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_EXTRACT_PROVIDERS) | set(_CUSTOM_EXTRACT_PROVIDERS))


def available_live_data_providers() -> list[str]:
    """Every Extract Template provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_LIVE_DATA_PROVIDERS) | set(_CUSTOM_LIVE_DATA_PROVIDERS))


def available_research_providers() -> list[str]:
    """Every research provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_RESEARCH_PROVIDERS) | set(_CUSTOM_RESEARCH_PROVIDERS))


def available_browser_providers() -> list[str]:
    """Every browser provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_BROWSER_PROVIDERS) | set(_CUSTOM_BROWSER_PROVIDERS))


def available_browse_providers() -> list[str]:
    """Every browse provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_BROWSE_PROVIDERS) | set(_CUSTOM_BROWSE_PROVIDERS))


def _resolve(
    name: str | Provider,
    *,
    builtin: dict[str, tuple[str, str, str]],
    custom: dict[str, type[_ProviderT]],
    available: list[str],
) -> type[_ProviderT]:
    """Look a name up in one method's tables, importing its module on first use."""
    key = provider_name(name)

    if key in custom:
        return custom[key]

    if key not in builtin:
        raise ProviderNotFoundError(key, available)

    module_path, class_name, extra = builtin[key]
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        # Only the missing SDK means "not installed". An ImportError from inside the provider module is a bug, and
        # reporting it as a missing extra would send the reader looking in entirely the wrong place.
        if exc.name and exc.name.startswith("web_router"):
            raise
        raise ProviderNotInstalledError(key, extra) from exc

    provider_cls: type[_ProviderT] = getattr(module, class_name)
    return provider_cls


def get_provider_class(name: str | Provider) -> type[SearchProvider]:
    """Resolve a search provider name to its class, importing the module on first use; ``exa:exa-instant`` pins."""
    resolve = partial(_resolve, builtin=_BUILTIN_PROVIDERS, custom=_CUSTOM_PROVIDERS, available=available_providers())
    return pinned_class(name, resolve)


def get_extract_provider_class(name: str | Provider) -> type[ExtractProvider]:
    """Resolve an extract provider name to its class, on first use; ``tavily:tavily-advanced`` pins."""
    tables: dict[str, Any] = {"builtin": _BUILTIN_EXTRACT_PROVIDERS, "custom": _CUSTOM_EXTRACT_PROVIDERS}
    return pinned_class(name, partial(_resolve, **tables, available=available_extract_providers()), "extract")


def create_provider(name: str | Provider, **kwargs: Any) -> SearchProvider:
    """Instantiate a search provider by name, passing ``kwargs`` to its constructor."""
    return get_provider_class(name)(**kwargs)


def create_extract_provider(name: str | Provider, **kwargs: Any) -> ExtractProvider:
    """Instantiate an extract provider by name, passing ``kwargs`` to its constructor."""
    return get_extract_provider_class(name)(**kwargs)


def get_live_data_provider_class(name: str | Provider) -> type[LiveDataProvider]:
    """Resolve an Extract Template provider name to its class, importing the module on first use."""
    return _resolve(
        name,
        builtin=_BUILTIN_LIVE_DATA_PROVIDERS,
        custom=_CUSTOM_LIVE_DATA_PROVIDERS,
        available=available_live_data_providers(),
    )


def create_live_data_provider(name: str | Provider, **kwargs: Any) -> LiveDataProvider:
    """Instantiate an Extract Template provider by name, passing ``kwargs`` to its constructor."""
    return get_live_data_provider_class(name)(**kwargs)


def get_research_provider_class(name: str | Provider) -> type[ResearchProvider]:
    """Resolve a research provider name to its class, importing the module on first use."""
    return _resolve(
        name,
        builtin=_BUILTIN_RESEARCH_PROVIDERS,
        custom=_CUSTOM_RESEARCH_PROVIDERS,
        available=available_research_providers(),
    )


def create_research_provider(name: str | Provider, **kwargs: Any) -> ResearchProvider:
    """Instantiate a research provider by name, passing ``kwargs`` to its constructor."""
    return get_research_provider_class(name)(**kwargs)


def get_browser_provider_class(name: str | Provider) -> type[BrowserProvider]:
    """Resolve a browser provider name to its class, importing the module on first use."""
    return _resolve(
        name,
        builtin=_BUILTIN_BROWSER_PROVIDERS,
        custom=_CUSTOM_BROWSER_PROVIDERS,
        available=available_browser_providers(),
    )


def create_browser_provider(name: str | Provider, **kwargs: Any) -> BrowserProvider:
    """Instantiate a browser provider by name, passing ``kwargs`` to its constructor."""
    return get_browser_provider_class(name)(**kwargs)


def get_browse_provider_class(name: str | Provider) -> type[BrowseProvider]:
    """Resolve a browse provider name to its class, on first use; ``skyvern:skyvern-2.0`` pins."""
    tables: dict[str, Any] = {"builtin": _BUILTIN_BROWSE_PROVIDERS, "custom": _CUSTOM_BROWSE_PROVIDERS}
    return pinned_class(name, partial(_resolve, **tables, available=available_browse_providers()), "browse")


def create_browse_provider(name: str | Provider, **kwargs: Any) -> BrowseProvider:
    """Instantiate a browse provider by name, passing ``kwargs`` to its constructor."""
    return get_browse_provider_class(name)(**kwargs)
