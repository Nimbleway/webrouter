"""The answer backends' registry: which backends serve ``answer()``, and which one an unnamed call uses.

``answer`` has a table of its own, because a vendor's answer endpoint is not its
search endpoint. It lives here rather than in :mod:`web_router.registry`, whose
lookup helpers it reuses; that module does not import this one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from web_router import registry
from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    EXTRA_ANSWER,
    PROVIDER_AUTO,
    PROVIDER_BRAVE,
    PROVIDER_EXA,
    PROVIDER_LINKUP,
    PROVIDER_PARALLEL,
    PROVIDER_PERPLEXITY,
    PROVIDER_YOU,
)
from web_router.exceptions import NoAnswerProviderError
from web_router.registry import _is_set, _resolve
from web_router.types import Provider, provider_name

if TYPE_CHECKING:
    from web_router.providers.answer import AnswerProvider

_BUILTIN_ANSWER_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_AUTO: ("web_router.providers.auto_answer", "AutoAnswerProvider", EXTRA_ANSWER),
    PROVIDER_PERPLEXITY: ("web_router.providers.perplexity_answer", "PerplexityAnswerProvider", EXTRA_ANSWER),
    PROVIDER_PARALLEL: ("web_router.providers.parallel", "ParallelAnswerProvider", EXTRA_ANSWER),
    PROVIDER_LINKUP: ("web_router.providers.linkup", "LinkupAnswerProvider", EXTRA_ANSWER),
    PROVIDER_YOU: ("web_router.providers.you_answer", "YouAnswerProvider", EXTRA_ANSWER),
    PROVIDER_EXA: ("web_router.providers.exa_answer", "ExaAnswerProvider", EXTRA_ANSWER),
    PROVIDER_BRAVE: ("web_router.providers.brave_answer", "BraveAnswerProvider", EXTRA_ANSWER),
}

_CUSTOM_ANSWER_PROVIDERS: dict[str, type[AnswerProvider]] = {}


def register_answer_provider(name: str | Provider, provider_cls: type[AnswerProvider]) -> None:
    """Register an answer provider class under ``name``, overriding any builtin of that name."""
    _CUSTOM_ANSWER_PROVIDERS[provider_name(name)] = provider_cls


def unregister_answer_provider(name: str | Provider) -> None:
    """Remove a previously registered custom answer provider. A no-op if it was never registered."""
    _CUSTOM_ANSWER_PROVIDERS.pop(provider_name(name), None)


def available_answer_providers() -> list[str]:
    """Every answer provider name the router knows, installed or not."""
    return sorted(set(_BUILTIN_ANSWER_PROVIDERS) | set(_CUSTOM_ANSWER_PROVIDERS))


def every_provider_name() -> list[str]:
    """Every backend name any method knows, installed or not: what a router's default provider may be."""
    return (
        registry.available_providers()
        + registry.available_extract_providers()
        + registry.available_template_providers()
        + registry.available_research_providers()
        + available_answer_providers()
        + registry.available_browser_providers()
        + registry.available_browse_providers()
    )


def configured_answer_providers() -> list[str]:
    """Every builtin answer provider you currently have credentials for.

    The same caveats as :func:`configured_providers`. Every answer backend
    bills the call, so with no key at all this is empty; ``auto`` is never
    listed, since it is these backends in turn.
    """
    return sorted(
        name for name, env_vars in ANSWER_ENV_BY_PROVIDER.items() if all(_is_set(env_var) for env_var in env_vars)
    )


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


def default_answer_provider(default: str, config: Mapping[str, Mapping[str, Any]]) -> str:
    """The answer backend an unnamed ``answer()`` call uses.

    The router's ``default`` when it serves answers -- ``auto`` included, which
    tries the configured backends in ranked order and raises when there are
    none -- else the one configured answer backend, a key passed in ``config``
    counting as one in the environment does. Otherwise raises
    :class:`NoAnswerProviderError`, naming the configured backends when there
    are several and the variables to set when there are none.
    """
    available = available_answer_providers()
    if default in available:
        return default
    # `auto` holds no key of its own: one set under it configures nothing.
    in_code = {
        name for name, entry in config.items() if name in available and name != PROVIDER_AUTO and entry.get("api_key")
    }
    configured = sorted(set(configured_answer_providers()) | in_code)
    if len(configured) == 1:
        return configured[0]
    env_vars = {var for name in available for var in ANSWER_ENV_BY_PROVIDER.get(name, ())}
    raise NoAnswerProviderError(default, available, configured, env_vars)
