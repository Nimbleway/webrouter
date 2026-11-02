"""Provider registry, one table per method.

Providers are looked up by name and imported lazily, so installing web-router
without a provider's optional dependency stays valid until that provider is
actually requested.

There is a table per method rather than per vendor, because a name does not
imply a capability: ``brave`` searches and cannot extract, ``oxylabs`` extracts
and cannot search, and ``nimble`` searches, extracts, runs templates
and researches through four different classes. ``answer`` has a table of its
own too, because a vendor's answer endpoint is not its search endpoint.
Asking the wrong table for a name reports it as unknown and lists the ones that
method does serve, which is the honest answer.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    AUTO_EXTRA,
    BROWSE_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    ENV_BY_PROVIDER,
    EXTRA_ALL,
    EXTRA_ANSWER,
    EXTRA_AWS,
    EXTRA_EXTRACT,
    EXTRA_RESEARCH,
    EXTRA_SEARCH,
    EXTRACT_ENV_BY_PROVIDER,
    PROVIDER_AGENTCORE,
    PROVIDER_ANCHOR,
    PROVIDER_AUTO,
    PROVIDER_BRAVE,
    PROVIDER_BRIGHTDATA,
    PROVIDER_BROWSER_USE,
    PROVIDER_BROWSERBASE,
    PROVIDER_DEEPSEEK,
    PROVIDER_EXA,
    PROVIDER_EXA_MCP,
    PROVIDER_FIRECRAWL,
    PROVIDER_HYPERBROWSER,
    PROVIDER_JINA,
    PROVIDER_KEENABLE,
    PROVIDER_KERNEL,
    PROVIDER_LINKUP,
    PROVIDER_LOCAL,
    PROVIDER_MAX,
    PROVIDER_NIMBLE,
    PROVIDER_OXYLABS,
    PROVIDER_PARALLEL,
    PROVIDER_PARALLEL_MCP,
    PROVIDER_PERPLEXITY,
    PROVIDER_SKYVERN,
    PROVIDER_STEEL,
    PROVIDER_TAVILY,
    PROVIDER_WEBIQ,
    PROVIDER_YOU,
    PROVIDER_YOU_MCP,
    RESEARCH_ENV_BY_PROVIDER,
    TEMPLATE_ENV_BY_PROVIDER,
)
from web_router.exceptions import NoAnswerProviderError, ProviderNotFoundError, ProviderNotInstalledError
from web_router.types import Provider, provider_name

if TYPE_CHECKING:
    from web_router.providers.answer import AnswerProvider
    from web_router.providers.base import (
        BrowseProvider,
        BrowserProvider,
        ExtractProvider,
        ResearchProvider,
        SearchProvider,
        TemplateProvider,
        _BaseProvider,
    )

_ProviderT = TypeVar("_ProviderT", bound="_BaseProvider")

# name -> (module path, class name, pip extra that supplies its SDK)
#
# Extras are per method, so every backend in a table points at the same one.
# The column stays because it is what ProviderNotInstalledError quotes back.
_BUILTIN_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_NIMBLE: ("web_router.providers.nimble", "NimbleProvider", EXTRA_SEARCH),
    PROVIDER_TAVILY: ("web_router.providers.tavily", "TavilyProvider", EXTRA_SEARCH),
    PROVIDER_YOU: ("web_router.providers.you", "YouProvider", EXTRA_SEARCH),
    PROVIDER_BRAVE: ("web_router.providers.brave", "BraveProvider", EXTRA_SEARCH),
    PROVIDER_EXA: ("web_router.providers.exa", "ExaProvider", EXTRA_SEARCH),
    PROVIDER_PARALLEL: ("web_router.providers.parallel", "ParallelProvider", EXTRA_SEARCH),
    PROVIDER_PERPLEXITY: ("web_router.providers.perplexity", "PerplexityProvider", EXTRA_SEARCH),
    PROVIDER_LINKUP: ("web_router.providers.linkup", "LinkupProvider", EXTRA_SEARCH),
    PROVIDER_WEBIQ: ("web_router.providers.webiq", "WebIQProvider", EXTRA_SEARCH),
    PROVIDER_KEENABLE: ("web_router.providers.keenable", "KeenableProvider", EXTRA_SEARCH),
    PROVIDER_DEEPSEEK: ("web_router.providers.deepseek", "DeepSeekProvider", EXTRA_SEARCH),
    PROVIDER_AGENTCORE: ("web_router.providers.agentcore", "AgentCoreProvider", EXTRA_AWS),
    PROVIDER_YOU_MCP: ("web_router.providers.you_mcp", "YouMcpProvider", EXTRA_SEARCH),
    PROVIDER_EXA_MCP: ("web_router.providers.exa_mcp", "ExaMcpProvider", EXTRA_SEARCH),
    PROVIDER_PARALLEL_MCP: ("web_router.providers.parallel_mcp", "ParallelMcpProvider", EXTRA_SEARCH),
    PROVIDER_AUTO: ("web_router.providers.auto", "AutoProvider", AUTO_EXTRA),
}

_BUILTIN_EXTRACT_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_NIMBLE: ("web_router.providers.nimble", "NimbleExtractProvider", EXTRA_EXTRACT),
    PROVIDER_TAVILY: ("web_router.providers.tavily", "TavilyExtractProvider", EXTRA_EXTRACT),
    PROVIDER_EXA: ("web_router.providers.exa", "ExaExtractProvider", EXTRA_EXTRACT),
    PROVIDER_LINKUP: ("web_router.providers.linkup", "LinkupExtractProvider", EXTRA_EXTRACT),
    PROVIDER_FIRECRAWL: ("web_router.providers.firecrawl", "FirecrawlExtractProvider", EXTRA_EXTRACT),
    PROVIDER_OXYLABS: ("web_router.providers.oxylabs", "OxylabsExtractProvider", EXTRA_EXTRACT),
    PROVIDER_BRIGHTDATA: ("web_router.providers.brightdata", "BrightDataExtractProvider", EXTRA_EXTRACT),
    PROVIDER_WEBIQ: ("web_router.providers.webiq", "WebIQExtractProvider", EXTRA_EXTRACT),
    PROVIDER_KEENABLE: ("web_router.providers.keenable", "KeenableExtractProvider", EXTRA_EXTRACT),
    PROVIDER_DEEPSEEK: ("web_router.providers.deepseek", "DeepSeekExtractProvider", EXTRA_EXTRACT),
    PROVIDER_JINA: ("web_router.providers.jina", "JinaExtractProvider", EXTRA_EXTRACT),
    PROVIDER_AUTO: ("web_router.providers.auto", "AutoExtractProvider", AUTO_EXTRA),
    PROVIDER_MAX: ("web_router.providers.max", "MaxExtractProvider", AUTO_EXTRA),
}

# A template run is the Extract API's, so the class ships with the extract extra.
# No `auto`: a template's name belongs to one vendor, so there is nothing to route.
_BUILTIN_TEMPLATE_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_NIMBLE: ("web_router.providers.nimble_template", "NimbleTemplateProvider", EXTRA_EXTRACT),
}

_BUILTIN_RESEARCH_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_NIMBLE: ("web_router.providers.nimble", "NimbleResearchProvider", EXTRA_RESEARCH),
    PROVIDER_PARALLEL: ("web_router.providers.parallel", "ParallelResearchProvider", EXTRA_RESEARCH),
    PROVIDER_PERPLEXITY: ("web_router.providers.perplexity", "PerplexityResearchProvider", EXTRA_RESEARCH),
    PROVIDER_LINKUP: ("web_router.providers.linkup", "LinkupResearchProvider", EXTRA_RESEARCH),
    PROVIDER_YOU: ("web_router.providers.you", "YouResearchProvider", EXTRA_RESEARCH),
    PROVIDER_AUTO: ("web_router.providers.auto", "AutoResearchProvider", AUTO_EXTRA),
}

# No `auto`: its order waits on a comparison of the backends' answers.
_BUILTIN_ANSWER_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_PERPLEXITY: ("web_router.providers.perplexity", "PerplexityAnswerProvider", EXTRA_ANSWER),
    PROVIDER_PARALLEL: ("web_router.providers.parallel", "ParallelAnswerProvider", EXTRA_ANSWER),
    PROVIDER_LINKUP: ("web_router.providers.linkup", "LinkupAnswerProvider", EXTRA_ANSWER),
    PROVIDER_YOU: ("web_router.providers.you", "YouAnswerProvider", EXTRA_ANSWER),
    PROVIDER_EXA: ("web_router.providers.exa", "ExaAnswerProvider", EXTRA_ANSWER),
}

# Opening a session needs only httpx, which `all` installs, so the vendors name
# `all` rather than `browser`: that extra is Playwright, for the handle's page
# actions, and opening a session does not need it. `local` needs only the
# standard library, so its column is never quoted.
_BUILTIN_BROWSER_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_STEEL: ("web_router.providers.steel", "SteelBrowserProvider", EXTRA_ALL),
    PROVIDER_BROWSERBASE: ("web_router.providers.browserbase", "BrowserbaseBrowserProvider", EXTRA_ALL),
    PROVIDER_HYPERBROWSER: ("web_router.providers.hyperbrowser", "HyperbrowserBrowserProvider", EXTRA_ALL),
    PROVIDER_ANCHOR: ("web_router.providers.anchor", "AnchorBrowserProvider", EXTRA_ALL),
    PROVIDER_KERNEL: ("web_router.providers.kernel", "KernelBrowserProvider", EXTRA_ALL),
    PROVIDER_LOCAL: ("web_router.providers.local", "LocalBrowserProvider", EXTRA_ALL),
    PROVIDER_AUTO: ("web_router.providers.auto", "AutoBrowserProvider", AUTO_EXTRA),
}

# Task backends are plain httpx, which `all` installs, as for the browser table.
_BUILTIN_BROWSE_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_BROWSER_USE: ("web_router.providers.browser_use", "BrowserUseBrowseProvider", EXTRA_ALL),
    PROVIDER_BROWSERBASE: ("web_router.providers.browserbase", "BrowserbaseBrowseProvider", EXTRA_ALL),
    PROVIDER_SKYVERN: ("web_router.providers.skyvern", "SkyvernBrowseProvider", EXTRA_ALL),
    PROVIDER_AUTO: ("web_router.providers.auto", "AutoBrowseProvider", AUTO_EXTRA),
}

_CUSTOM_PROVIDERS: dict[str, type[SearchProvider]] = {}
_CUSTOM_EXTRACT_PROVIDERS: dict[str, type[ExtractProvider]] = {}
_CUSTOM_TEMPLATE_PROVIDERS: dict[str, type[TemplateProvider]] = {}
_CUSTOM_RESEARCH_PROVIDERS: dict[str, type[ResearchProvider]] = {}
_CUSTOM_ANSWER_PROVIDERS: dict[str, type[AnswerProvider]] = {}
_CUSTOM_BROWSER_PROVIDERS: dict[str, type[BrowserProvider]] = {}
_CUSTOM_BROWSE_PROVIDERS: dict[str, type[BrowseProvider]] = {}


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


def register_template_provider(name: str | Provider, provider_cls: type[TemplateProvider]) -> None:
    """Register an Extract Template provider class under ``name``, overriding any builtin of that name."""
    _CUSTOM_TEMPLATE_PROVIDERS[provider_name(name)] = provider_cls


def unregister_template_provider(name: str | Provider) -> None:
    """Remove a previously registered custom template provider. A no-op if it was never registered."""
    _CUSTOM_TEMPLATE_PROVIDERS.pop(provider_name(name), None)


def register_research_provider(name: str | Provider, provider_cls: type[ResearchProvider]) -> None:
    """Register a research provider class under ``name``, overriding any builtin of that name."""
    _CUSTOM_RESEARCH_PROVIDERS[provider_name(name)] = provider_cls


def unregister_research_provider(name: str | Provider) -> None:
    """Remove a previously registered custom research provider. A no-op if it was never registered."""
    _CUSTOM_RESEARCH_PROVIDERS.pop(provider_name(name), None)


def register_answer_provider(name: str | Provider, provider_cls: type[AnswerProvider]) -> None:
    """Register an answer provider class under ``name``, overriding any builtin of that name."""
    _CUSTOM_ANSWER_PROVIDERS[provider_name(name)] = provider_cls


def unregister_answer_provider(name: str | Provider) -> None:
    """Remove a previously registered custom answer provider. A no-op if it was never registered."""
    _CUSTOM_ANSWER_PROVIDERS.pop(provider_name(name), None)


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


def available_template_providers() -> list[str]:
    """Every Extract Template provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_TEMPLATE_PROVIDERS) | set(_CUSTOM_TEMPLATE_PROVIDERS))


def available_research_providers() -> list[str]:
    """Every research provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_RESEARCH_PROVIDERS) | set(_CUSTOM_RESEARCH_PROVIDERS))


def available_answer_providers() -> list[str]:
    """Every answer provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_ANSWER_PROVIDERS) | set(_CUSTOM_ANSWER_PROVIDERS))


def available_browser_providers() -> list[str]:
    """Every browser provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_BROWSER_PROVIDERS) | set(_CUSTOM_BROWSER_PROVIDERS))


def available_browse_providers() -> list[str]:
    """Every browse provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_BROWSE_PROVIDERS) | set(_CUSTOM_BROWSE_PROVIDERS))


def _is_set(env_var: str) -> bool:
    """Whether ``env_var`` holds a value, counting whitespace-only as unset, as the providers do."""
    return bool((os.environ.get(env_var) or "").strip())


def configured_providers() -> list[str]:
    """Every builtin search provider you currently have a credential for, plus the keyless ones.

    Reports what the environment makes usable, which
    :func:`available_providers` deliberately does not: that lists every name
    the router knows, key or no key.

    This reads environment variables only. A key passed as ``provider_config``
    is invisible here, and a key that is present may still be rejected by the
    service -- so treat this as "worth trying", not as a guarantee, and let the
    provider itself raise when a credential is actually missing or bad.
    """
    return sorted(name for name, env_var in ENV_BY_PROVIDER.items() if env_var is None or _is_set(env_var))


def configured_extract_providers() -> list[str]:
    """Every builtin extract provider you currently have credentials for, plus the keyless ones.

    The same caveats as :func:`configured_providers`. A backend needing more
    than one variable -- Oxylabs its username and password, Bright Data its
    token and zone -- is reported only when it has all of them.
    """
    return sorted(
        name for name, env_vars in EXTRACT_ENV_BY_PROVIDER.items() if all(_is_set(env_var) for env_var in env_vars)
    )


def configured_template_providers() -> list[str]:
    """Every builtin template provider you currently have credentials for.

    The same caveats as :func:`configured_providers`. A template run fetches
    and parses on the backend's account, so there is no keyless member and
    no ``auto``; with no key at all this is empty.
    """
    return sorted(
        name for name, env_vars in TEMPLATE_ENV_BY_PROVIDER.items() if all(_is_set(env_var) for env_var in env_vars)
    )


def configured_research_providers() -> list[str]:
    """Every builtin research provider you currently have credentials for.

    The same caveats as :func:`configured_providers`. Shorter than the other two
    in practice: every research backend bills for the run, so there is no
    keyless member to pad it out, and with no key at all this is empty but for
    ``auto`` -- which will then say which variables would fix it.
    """
    return sorted(
        name for name, env_vars in RESEARCH_ENV_BY_PROVIDER.items() if all(_is_set(env_var) for env_var in env_vars)
    )


def configured_answer_providers() -> list[str]:
    """Every builtin answer provider you currently have credentials for.

    The same caveats as :func:`configured_providers`. Every answer backend
    bills the call and there is no ``auto`` yet, so with no key at all this is
    empty. ``answer()`` with no provider named uses the one backend listed
    here when there is exactly one.
    """
    return sorted(
        name for name, env_vars in ANSWER_ENV_BY_PROVIDER.items() if all(_is_set(env_var) for env_var in env_vars)
    )


def configured_browser_providers() -> list[str]:
    """Every builtin browser provider you currently have credentials for.

    The same caveats as :func:`configured_providers`. A cloud browser bills
    for every minute it is open; ``local`` needs no key and is always listed,
    whether or not a browser is installed for it to launch.
    """
    return sorted(
        name for name, env_vars in BROWSER_ENV_BY_PROVIDER.items() if all(_is_set(env_var) for env_var in env_vars)
    )


def configured_browse_providers() -> list[str]:
    """Every builtin browse provider you currently have credentials for.

    The same caveats as :func:`configured_providers`. No keyless tier: every
    task backend bills for the run, so with no key at all this is empty but for
    ``auto`` -- which will then say which variables would fix it.
    """
    return sorted(
        name for name, env_vars in BROWSE_ENV_BY_PROVIDER.items() if all(_is_set(env_var) for env_var in env_vars)
    )


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
        # Only the missing SDK means "not installed". An ImportError from inside
        # the provider module is a bug, and reporting it as a missing extra would
        # send the reader looking in entirely the wrong place.
        if exc.name and exc.name.startswith("web_router"):
            raise
        raise ProviderNotInstalledError(key, extra) from exc

    provider_cls: type[_ProviderT] = getattr(module, class_name)
    return provider_cls


def get_provider_class(name: str | Provider) -> type[SearchProvider]:
    """Resolve a search provider name to its class, importing the module on first use."""
    return _resolve(
        name,
        builtin=_BUILTIN_PROVIDERS,
        custom=_CUSTOM_PROVIDERS,
        available=available_providers(),
    )


def get_extract_provider_class(name: str | Provider) -> type[ExtractProvider]:
    """Resolve an extract provider name to its class, importing the module on first use."""
    return _resolve(
        name,
        builtin=_BUILTIN_EXTRACT_PROVIDERS,
        custom=_CUSTOM_EXTRACT_PROVIDERS,
        available=available_extract_providers(),
    )


def create_provider(name: str | Provider, **kwargs: Any) -> SearchProvider:
    """Instantiate a search provider by name, passing ``kwargs`` to its constructor."""
    return get_provider_class(name)(**kwargs)


def create_extract_provider(name: str | Provider, **kwargs: Any) -> ExtractProvider:
    """Instantiate an extract provider by name, passing ``kwargs`` to its constructor."""
    return get_extract_provider_class(name)(**kwargs)


def get_template_provider_class(name: str | Provider) -> type[TemplateProvider]:
    """Resolve an Extract Template provider name to its class, importing the module on first use."""
    return _resolve(
        name,
        builtin=_BUILTIN_TEMPLATE_PROVIDERS,
        custom=_CUSTOM_TEMPLATE_PROVIDERS,
        available=available_template_providers(),
    )


def create_template_provider(name: str | Provider, **kwargs: Any) -> TemplateProvider:
    """Instantiate an Extract Template provider by name, passing ``kwargs`` to its constructor."""
    return get_template_provider_class(name)(**kwargs)


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


def get_answer_provider_class(name: str | Provider) -> type[AnswerProvider]:
    """Resolve an answer provider name to its class, importing the module on first use."""
    return _resolve(
        name,
        builtin=_BUILTIN_ANSWER_PROVIDERS,
        custom=_CUSTOM_ANSWER_PROVIDERS,
        available=available_answer_providers(),
    )


def create_answer_provider(name: str | Provider, **kwargs: Any) -> AnswerProvider:
    """Instantiate an answer provider by name, passing ``kwargs`` to its constructor."""
    return get_answer_provider_class(name)(**kwargs)


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
    """Resolve a browse provider name to its class, importing the module on first use."""
    return _resolve(
        name,
        builtin=_BUILTIN_BROWSE_PROVIDERS,
        custom=_CUSTOM_BROWSE_PROVIDERS,
        available=available_browse_providers(),
    )


def create_browse_provider(name: str | Provider, **kwargs: Any) -> BrowseProvider:
    """Instantiate a browse provider by name, passing ``kwargs`` to its constructor."""
    return get_browse_provider_class(name)(**kwargs)


def default_answer_provider(default: str, config: Mapping[str, Mapping[str, Any]]) -> str:
    """The answer backend an unnamed ``answer()`` call uses, with no ``auto`` to fall back on.

    The router's ``default`` when it serves answers, else the one configured
    answer backend -- a key passed in ``config`` counts, as one in the
    environment does. Otherwise raises :class:`NoAnswerProviderError`, naming
    the configured backends when there are several and the variables to set
    when there are none.
    """
    available = available_answer_providers()
    if default in available:
        return default
    in_code = {name for name, entry in config.items() if name in available and entry.get("api_key")}
    configured = sorted(set(configured_answer_providers()) | in_code)
    if len(configured) == 1:
        return configured[0]
    env_vars = {var for name in available for var in ANSWER_ENV_BY_PROVIDER.get(name, ())}
    raise NoAnswerProviderError(default, available, configured, env_vars)
