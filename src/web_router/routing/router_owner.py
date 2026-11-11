"""A router the routers built by name: held so closing them closes it, and never one the caller passed in.

Experimental. :class:`RouterOwner` is the part of
:class:`~web_router.routing.routed.RouterSetup` that keeps that router and
closes it, from sync or async code, or at once when attaching it fails.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from web_router.constants_invoke import MSG_ROUTER_DISCARD_FAILED
from web_router.routing.invoke import RouterLike
from web_router.routing.registry import aclose_router, discard_router, resolve_router


def _discard_refused(router: RouterLike | None) -> None:
    """Close a router whose owner refused to start: the refusal says why, so a failed close warns by its class."""
    try:
        discard_router(router)
    except Exception as exc:
        warnings.warn(MSG_ROUTER_DISCARD_FAILED.format(kind=type(exc).__name__), ResourceWarning, stacklevel=4)


class RouterOwner:
    """The router built here by name, if any, and its close."""

    _owned_router: RouterLike | None = None

    @contextmanager
    def _built(self, router: RouterLike | str | None, config: Mapping[str, Any] | None) -> Iterator[RouterLike | None]:
        """The router to attach, built here when ``router`` is a name: kept to close later, or closed if refused.

        Raises:
            ConfigurationError: for a name no router is registered under, a factory that returns ``None``, or
                ``router_config`` without a name.
        """
        attached, owned = resolve_router(router, config)
        try:
            yield attached
        except BaseException:
            _discard_refused(owned)  # built here and refused: nothing else would close it
            raise
        self._owned_router = owned

    def _close_refused_router(self) -> None:
        """Close the router built here when the routers refuse to start; a close that fails only warns."""
        _discard_refused(self._owned_router)

    def _close_router(self) -> None:
        """Close the router when it was built here by name; one passed in is the caller's to close."""
        discard_router(self._owned_router)  # an async-only router's aclose(), run here, included

    async def _aclose_router(self) -> None:
        """:meth:`_close_router` from async code: the router's ``aclose()``, else its ``close()``."""
        await aclose_router(self._owned_router)
