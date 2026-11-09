"""The library methods behind the MCP tools that pick a provider: each one's registry, classes, options and keys.

One table, so adding a method is one entry, and the capabilities resource,
the schemas and the no-backend hints all read the same facts.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    BROWSE_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    DELEGATING_PROVIDERS,
    ENV_BY_PROVIDER,
    EXTRACT_ENV_BY_PROVIDER,
    MCP_METHOD_BROWSER,
    MCP_METHOD_TEMPLATE,
    MCP_TOOL_ANSWER,
    MCP_TOOL_BROWSE,
    MCP_TOOL_BROWSER_OPEN,
    MCP_TOOL_EXTRACT,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_RUN_TEMPLATE,
    MCP_TOOL_SEARCH,
    MCP_TOOL_TEMPLATE,
    MCP_TOOL_TEMPLATES,
    RESEARCH_ENV_BY_PROVIDER,
    TEMPLATE_ENV_BY_PROVIDER,
)
from web_router.mcp.env import env_names
from web_router.providers.base import _BaseProvider
from web_router.registry import (
    configured_browse_providers,
    configured_browser_providers,
    configured_extract_providers,
    configured_providers,
    configured_research_providers,
    configured_template_providers,
    get_browse_provider_class,
    get_browser_provider_class,
    get_extract_provider_class,
    get_provider_class,
    get_research_provider_class,
    get_template_provider_class,
)
from web_router.registry_answer import configured_answer_providers, get_answer_provider_class
from web_router.types import (
    BROWSE_PARAMETERS,
    BROWSER_PARAMETERS,
    EXTRACT_PARAMETERS,
    NORMALIZED_PARAMETERS,
    RESEARCH_PARAMETERS,
    TEMPLATE_PARAMETERS,
)
from web_router.types_answer import ANSWER_PARAMETERS

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class Method:
    """What a provider-picking tool needs to know about the library method behind it."""

    key: str
    """How the capabilities resource names the method: the tool's own name, but ``template`` and ``open_browser``."""
    configured: Callable[[], list[str]]
    """Which backends this install can actually serve it with."""
    cls: Callable[[str], type[_BaseProvider]]
    """The class lookup, which reads ``supported_parameters`` without a credential."""
    env: Mapping[str, Any]
    """The method's ``*_ENV_BY_PROVIDER`` table."""
    options: frozenset[str] = frozenset()
    """The arguments a backend may or may not map, as opposed to the ones every backend takes."""


_TEMPLATE_METHOD = Method(
    MCP_METHOD_TEMPLATE, configured_template_providers, get_template_provider_class, TEMPLATE_ENV_BY_PROVIDER
)

METHODS: dict[str, Method] = {
    MCP_TOOL_SEARCH: Method(
        MCP_TOOL_SEARCH, configured_providers, get_provider_class, ENV_BY_PROVIDER, NORMALIZED_PARAMETERS
    ),
    MCP_TOOL_EXTRACT: Method(
        MCP_TOOL_EXTRACT,
        configured_extract_providers,
        get_extract_provider_class,
        EXTRACT_ENV_BY_PROVIDER,
        EXTRACT_PARAMETERS,
    ),
    # Listing and describing take no option a backend may decline; a run takes the template's.
    MCP_TOOL_TEMPLATES: _TEMPLATE_METHOD,
    MCP_TOOL_TEMPLATE: _TEMPLATE_METHOD,
    MCP_TOOL_RUN_TEMPLATE: replace(_TEMPLATE_METHOD, options=TEMPLATE_PARAMETERS),
    MCP_TOOL_ANSWER: Method(
        MCP_TOOL_ANSWER,
        configured_answer_providers,
        get_answer_provider_class,
        ANSWER_ENV_BY_PROVIDER,
        ANSWER_PARAMETERS,
    ),
    MCP_TOOL_RESEARCH: Method(
        MCP_TOOL_RESEARCH,
        configured_research_providers,
        get_research_provider_class,
        RESEARCH_ENV_BY_PROVIDER,
        RESEARCH_PARAMETERS,
    ),
    MCP_TOOL_BROWSE: Method(
        MCP_TOOL_BROWSE,
        configured_browse_providers,
        get_browse_provider_class,
        BROWSE_ENV_BY_PROVIDER,
        BROWSE_PARAMETERS,
    ),
    MCP_TOOL_BROWSER_OPEN: Method(
        MCP_METHOD_BROWSER,
        configured_browser_providers,
        get_browser_provider_class,
        BROWSER_ENV_BY_PROVIDER,
        BROWSER_PARAMETERS,
    ),
}
"""The nine tools that reach a backend, and the method each serves.

The two ``*_status`` tools, ``fetch_raw`` and the browser actions work on what
an earlier call left behind and never pick a provider. The three template
tools share one method.
"""

EACH_METHOD: tuple[Method, ...] = tuple({method.key: method for method in METHODS.values()}.values())
"""Every method once, in the order the capabilities resource lists them."""


def configured() -> dict[str, list[str]]:
    """The backends configured here, per method, keyed as the capabilities resource names them."""
    return {method.key: method.configured() for method in EACH_METHOD}


def unlock() -> dict[str, dict[str, list[str]]]:
    """Per method, the backends no key has reached yet and the variables that would add each -- names only."""
    unlock: dict[str, dict[str, list[str]]] = {}
    for method in EACH_METHOD:
        configured = set(method.configured())
        missing = {
            provider: [name for name in env_names(env) if not os.environ.get(name)]
            for provider, env in method.env.items()
            if env and provider not in configured
        }
        if missing:
            unlock[method.key] = missing
    return unlock


def keyed(method: Method) -> list[str]:
    """The backends configured for this method that needed a credential to be."""
    return [provider for provider in method.configured() if method.env.get(provider)]


def usable(method: Method) -> bool:
    """Whether anything but a delegating provider is configured for this method.

    ``auto`` and ``max`` are always "configured" because they need no credential
    of their own -- they need whatever their chain does. For search and extract
    that chain ends in a keyless backend and always works; for research and
    browse every backend bills, so ``auto`` alone means nothing here can
    actually run. Answer's ``auto`` is not listed among its backends, so it is usable exactly when a
    key is there.
    """
    return any(provider not in DELEGATING_PROVIDERS for provider in method.configured())


def env_vars(method: Method) -> list[str]:
    """Every variable that would configure a backend for this method."""
    return sorted({name for env in method.env.values() for name in env_names(env)})
