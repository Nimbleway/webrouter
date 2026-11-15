"""The errors for a provider name the router cannot serve.

Import them from :mod:`web_router.exceptions`, which re-exports both.
"""

from __future__ import annotations

from collections.abc import Iterable

from web_router.constants import MSG_UNKNOWN_PROVIDER
from web_router.constants_answer import MSG_ANSWER_NO_DEFAULT, MSG_ANSWER_NONE_CONFIGURED
from web_router.constants_providers import MSG_PROVIDER_WRONG_METHOD
from web_router.exceptions import WebRouterError
from web_router.types import Provider

_BUILTIN_NAMES = frozenset(provider.value for provider in Provider)


class ProviderNotFoundError(WebRouterError):
    """A provider was requested by a name that is not registered."""

    def __init__(self, name: str, available: Iterable[str]) -> None:
        self.name = name
        # Deduplicated: the router looks a default up in both method registries,
        # and a provider serving both would otherwise be listed twice.
        self.available = sorted(set(available))
        # A built-in name is a real provider asked for a method it does not serve, not a typo.
        template = MSG_PROVIDER_WRONG_METHOD if name in _BUILTIN_NAMES else MSG_UNKNOWN_PROVIDER
        super().__init__(template.format(name=name, available=", ".join(self.available)))


class NoAnswerProviderError(ProviderNotFoundError):
    """``answer()`` was called with no provider, and none could be chosen, or ``auto`` had no backend to try.

    A :class:`ProviderNotFoundError`, so an ``except`` for that still catches it.
    :attr:`configured` names the answer backends with a key -- several, so which
    to call is the caller's choice -- and when there are none, :attr:`env_vars`
    names the variables that would add one.
    """

    def __init__(self, default: str, available: Iterable[str], configured: Iterable[str], env_vars: Iterable[str]):
        self.name = default
        self.available = sorted(set(available))
        self.configured = sorted(configured)
        self.env_vars = sorted(env_vars)
        if self.configured:
            message = MSG_ANSWER_NO_DEFAULT.format(
                default=default, count=len(self.configured), available=", ".join(self.configured)
            )
        else:
            message = MSG_ANSWER_NONE_CONFIGURED.format(default=default, env_vars=", ".join(self.env_vars) or "none")
        WebRouterError.__init__(self, message)
