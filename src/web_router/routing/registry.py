"""Routers by name: ``register_router(name, factory)``, and ``router="name"`` with ``router_config``.

Experimental. A name maps to a factory, called with ``router_config``'s
entries as keyword arguments when a router is built; what it returns is
attached as any ``router=`` object is. ``remote``, ``llm`` and ``systemone`` are
built in and imported only when used; registering a name overrides a built-in one of that name, and
unregistering it brings the built-in back.

A router the routers build by name is theirs: closing them calls its
``close()``, or from :class:`~web_router.AsyncWebRouter` its ``aclose()``,
when it has one. A router passed in already built is the caller's to close,
as a provider passed in is.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Mapping
from typing import Any

from web_router.constants_decider import LLM_ROUTER_NAME, SYSTEMONE_ROUTER_NAME
from web_router.constants_invoke import (
    MSG_ROUTER_BUILT_NONE,
    MSG_ROUTER_CONFIG_MISSING,
    MSG_ROUTER_CONFIG_UNKNOWN,
    MSG_ROUTER_CONFIG_UNNAMED,
    MSG_ROUTER_FACTORY,
    MSG_ROUTER_NAME,
    MSG_ROUTER_UNKNOWN,
)
from web_router.constants_remote import REMOTE_ROUTER_NAME
from web_router.exceptions import ConfigurationError
from web_router.routing._runner import _on_private_loop
from web_router.routing.invoke import RouterLike

RouterFactory = Callable[..., RouterLike]
"""What :func:`register_router` takes: called with ``router_config``'s entries as keyword arguments."""

_BUILTIN_ROUTERS: dict[str, tuple[str, str]] = {
    REMOTE_ROUTER_NAME: ("web_router.routing.remote", "RemoteRouter"),
    LLM_ROUTER_NAME: ("web_router.routing.decider_llm", "llm_router"),
    SYSTEMONE_ROUTER_NAME: ("web_router.routing.decider_systemone", "systemone_router"),
}
"""Built-in name -> (module, factory), imported on first use so ``import web_router`` pulls in none of them."""

_CUSTOM_ROUTERS: dict[str, RouterFactory] = {}


def _checked_name(name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError(MSG_ROUTER_NAME.format(name=name))
    return name.strip()


def register_router(name: str, factory: RouterFactory) -> None:
    """Register ``factory`` under ``name`` for ``router="name"``, overriding any built-in router of that name.

    Raises:
        ConfigurationError: for a name that is not a non-empty string, or a factory that is not callable.
    """
    key = _checked_name(name)
    if not callable(factory):
        raise ConfigurationError(MSG_ROUTER_FACTORY.format(name=key, kind=type(factory).__name__))
    _CUSTOM_ROUTERS[key] = factory


def unregister_router(name: str) -> None:
    """Remove a router registered under ``name``. A no-op if none was; a built-in router of that name is back."""
    _CUSTOM_ROUTERS.pop(_checked_name(name), None)


def available_routers() -> list[str]:
    """Every router name ``router=`` takes: the built-in ones and those registered."""
    return sorted({*_BUILTIN_ROUTERS, *_CUSTOM_ROUTERS})


def _factory(key: str) -> RouterFactory:
    if key in _CUSTOM_ROUTERS:
        return _CUSTOM_ROUTERS[key]
    if key in _BUILTIN_ROUTERS:
        module_path, attribute = _BUILTIN_ROUTERS[key]
        found: RouterFactory = getattr(importlib.import_module(module_path), attribute)
        return found
    raise ConfigurationError(MSG_ROUTER_UNKNOWN.format(name=key, known=", ".join(available_routers())))


def _fitted(key: str, factory: RouterFactory, config: dict[str, Any]) -> dict[str, Any]:
    """``config``, once it fits ``factory``'s parameters, so a misspelled key is a configuration error.

    Only the arguments are checked, never the call: a ``TypeError`` raised in the factory's own body is a bug
    and propagates. The error names the keys, never their values.

    Raises:
        ConfigurationError: for a key the factory does not take, or one it needs that is missing.
    """
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):  # no signature to read: the call itself decides
        return config
    try:
        signature.bind(**config)
    except TypeError:
        named = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        params = signature.parameters.values()
        takes = [p.name for p in params if p.kind in named]
        open_ended = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
        unknown = [] if open_ended else sorted(repr(name) for name in config if name not in takes)
        needed = [repr(p.name) for p in params if p.kind in named and p.default is p.empty and p.name not in config]
        message = (
            MSG_ROUTER_CONFIG_UNKNOWN.format(name=key, keys=", ".join(unknown), takes=", ".join(takes) or "none")
            if unknown
            else MSG_ROUTER_CONFIG_MISSING.format(name=key, keys=", ".join(needed))
        )
        raise ConfigurationError(message) from None
    return config


def resolve_router(
    router: RouterLike | str | None, router_config: Mapping[str, Any] | None
) -> tuple[RouterLike | None, RouterLike | None]:
    """The router to attach, and the same router again when it was built here by name, so its owner closes it.

    Raises:
        ConfigurationError: for a name no router is registered under, a ``router_config`` key its factory does
            not take or one it needs and is not given, a factory that returns ``None``, or ``router_config``
            without a name.
    """
    if not isinstance(router, str):
        if router_config is not None:
            raise ConfigurationError(MSG_ROUTER_CONFIG_UNNAMED)
        return router, None
    key = _checked_name(router)
    factory = _factory(key)
    built = factory(**_fitted(key, factory, dict(router_config or {})))
    if built is None:  # attached as is, None would turn routing off without a word
        raise ConfigurationError(MSG_ROUTER_BUILT_NONE.format(name=key))
    return built, built


def close_router(router: object) -> None:
    """Call an owned router's ``close()``, when it has one."""
    close = getattr(router, "close", None)
    if callable(close):
        close()


def discard_router(router: object) -> None:
    """Close a router built here from sync code: its ``close()``, else its ``aclose()``, awaited before this returns.

    An ``aclose()`` runs on web-router's private loop, where a sync caller's async router ran its calls, so it
    is finished -- or its error raised -- whether or not this thread has a loop running.
    """
    if callable(getattr(router, "close", None)):
        close_router(router)
        return
    aclose = getattr(router, "aclose", None)
    result = aclose() if callable(aclose) else None
    if inspect.isawaitable(result):
        _on_private_loop(result).result()


async def aclose_router(router: object) -> None:
    """Await an owned router's ``aclose()``, else call its ``close()``, when it has either."""
    aclose = getattr(router, "aclose", None)
    if callable(aclose):
        result = aclose()
        if inspect.isawaitable(result):
            await result
        return
    close_router(router)
