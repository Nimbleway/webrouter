"""Pinning a vendor mode: ``provider="exa:exa-instant"``, or the same id as a chain entry.

A pinned id builds the backend's class with the mode stamped on every request: the depth slot
fills ``search_depth`` (or ``effort``), and the request's ``mode`` is the canonical string, so the
price list prices it -- before the call, where a budget charges it, and on the answer. A pinned id
without a config of its own is built with its backend's. :mod:`web_router.providers._modes`
merges the mode into the payload and refuses what it would overwrite.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import cache
from typing import Any, ClassVar, cast

from web_router.constants_cost import COST_ESTIMATED
from web_router.constants_modes import (
    DEPTH_LEVELS,
    EFFORT_LEVELS,
    ID_SEP,
    MODE_FIELD,
    MSG_PIN_EFFORT,
    MSG_PIN_TOOL,
    TOOLS,
)
from web_router.exceptions_typed import InvalidParameterError
from web_router.modes import Declared, Slots, parse_id
from web_router.providers._modes import SEARCH, refuse_depth, slots
from web_router.types import SearchRequest, provider_name


def pinned_request(owner: Any, request: SearchRequest, mode: str) -> SearchRequest:
    """``request`` in ``mode``: its level fills ``search_depth`` or ``effort``, and ``mode`` is the canonical string."""
    found = slots(owner, mode)
    update: dict[str, Any] = {}
    content = owner.contents.get(found.depth) if found.depth else None
    level = content.as_depth if content else found.depth  # a content stands for its level: its limits apply
    if level in DEPTH_LEVELS:
        refuse_depth(mode, level, request.search_depth)
        update["search_depth"] = level
    elif found.depth in EFFORT_LEVELS:
        if request.effort not in (None, found.depth):
            message = MSG_PIN_EFFORT.format(mode=mode, effort=found.depth, asked=request.effort)
            raise InvalidParameterError(MODE_FIELD, message)
        update["effort"] = found.depth
    update["mode"] = pinned_mode(found, request.search_depth)
    return request.model_copy(update=update)


def pinned_mode(found: Slots, depth: str | None) -> str | None:
    """The canonical mode a pin's ``found`` slots send at ``depth``: the pin's own depth slot wins."""
    return Slots(found.tier, found.depth or depth, found.flags).mode


def pinned_for(chain: Any, request: Any) -> Any:
    """``request`` in ``chain``'s pinned mode when ``chain`` is a pinned search backend, else unchanged.

    The router applies it before a call is priced, so a budget charges the mode's price, not the default's.
    """
    pinned = getattr(chain, "pinned", None)
    return pinned_request(chain, request, pinned) if pinned and isinstance(request, SearchRequest) else request


def _costed(response: Any, owner: Any, request: SearchRequest) -> Any:
    """``response`` carrying the mode's estimated price when it states none, as a chain member's answer must."""
    from web_router.routing.metered import estimated_cost  # the routing package imports the providers

    if response.cost_usd is not None or (cost := estimated_cost(SEARCH, owner.name, request)) is None:
        return response
    return response.model_copy(update={"cost_usd": cost, "cost_source": COST_ESTIMATED})


class _PinnedSearch:
    """Stamps the class's ``pinned`` mode on every search, searches as the backend does, and prices the answer."""

    pinned: ClassVar[str]

    def search(self, request: SearchRequest) -> Any:
        request = pinned_request(self, request, self.pinned)
        return _costed(super().search(request), self, request)  # type: ignore[misc]

    async def asearch(self, request: SearchRequest) -> Any:
        request = pinned_request(self, request, self.pinned)
        return _costed(await super().asearch(request), self, request)  # type: ignore[misc]


@cache
def _pinned(base: type, mode: str) -> type:
    return type(base.__name__, (_PinnedSearch, base), {"pinned": mode, "__module__": base.__module__})


def split_pin(name: str) -> tuple[str, str]:
    """A pinned id as its backend and its mode as written: ``search:exa:exa-instant`` is ``exa``, ``exa-instant``."""
    parts = name.split(ID_SEP)
    parts = parts[1:] if len(parts) > 1 and parts[0] in TOOLS else parts
    return parts[0], ID_SEP.join(parts[1:])


def backend_of(name: str) -> str:
    """The backend a pinned id names (``exa`` for ``search:exa:exa-instant``); any other name as it is."""
    return split_pin(name)[0]


def config_of(configs: Mapping[str, Any], name: str) -> Any:
    """``name``'s entry in ``configs``, keyed by backend: a pinned id without its own takes its backend's."""
    return configs.get(name, configs.get(backend_of(name), {}))


def pinned_class(name: Any, resolve: Callable[[str], type]) -> type:
    """The class for ``name``: ``resolve(name)``, or for ``provider:mode`` that class pinned to the mode."""
    text = provider_name(name)
    if ID_SEP not in text:
        return resolve(text)
    found = parse_id(text, cast("Callable[[str], Declared]", resolve))
    if found.tool not in (None, SEARCH):
        raise InvalidParameterError(MODE_FIELD, MSG_PIN_TOOL.format(text=text, tool=found.tool))
    base = resolve(found.provider)
    return _pinned(base, found.mode) if found.mode else base
