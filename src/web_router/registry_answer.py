"""The answer backends' registry: which backends serve ``answer()``, and which one an unnamed call uses.

Apart from :mod:`web_router.registry`, whose helpers it uses, as ``answer``
has a table of its own: a vendor's answer endpoint is not its search endpoint.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    EXTRA_ANSWER,
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

# No `auto`: its order waits on a comparison of the backends' answers.
_BUILTIN_ANSWER_PROVIDERS: dict[str, tuple[str, str, str]] = {
    PROVIDER_PERPLEXITY: ("web_router.providers.perplexity", "PerplexityAnswerProvider", EXTRA_ANSWER),
    PROVIDER_PARALLEL: ("web_router.providers.parallel", "ParallelAnswerProvider", EXTRA_ANSWER),
    PROVIDER_LINKUP: ("web_router.providers.linkup", "LinkupAnswerProvider", EXTRA_ANSWER),
    PROVIDER_YOU: ("web_router.providers.you", "YouAnswerProvider", EXTRA_ANSWER),
    PROVIDER_EXA: ("web_router.providers.exa", "ExaAnswerProvider", EXTRA_ANSWER),
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
