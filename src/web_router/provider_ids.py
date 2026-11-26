"""Checking a provider id before any call: what a router's constructor runs, and what an integration can run alone.

An id that names an unknown backend or a mode part its backend does not declare (``exa:exa-instnat``) fails here,
the same way the method it is used for would fail on its first call, so a config is checked without building a
router or spending anything. A tool given, or named by the id (``search:exa:exa-instant``), is the only one checked;
otherwise the id passes when any method that knows the backend takes it, as a router's default must.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any

from web_router import registry
from web_router.constants_modes import (
    ID_SEP,
    MODE_FIELD,
    MSG_ID_SHAPE,
    MSG_PIN_NONE,
    MSG_PIN_TOOL,
    MSG_TOOL_UNKNOWN,
    PINNED_CALLS,
    TOOLS,
)
from web_router.exceptions import ProviderNotFoundError, ProviderNotInstalledError
from web_router.exceptions_typed import InvalidParameterError
from web_router.providers._pinning import split_pin
from web_router.registry_answer import available_answer_providers, every_provider_name, get_answer_provider_class
from web_router.types import Provider, provider_name

_CLASSES: dict[str, tuple[Callable[[str], Any], Callable[[], list[str]]]] = {
    "search": (registry.get_provider_class, registry.available_providers),
    "extract": (registry.get_extract_provider_class, registry.available_extract_providers),
    "answer": (get_answer_provider_class, available_answer_providers),
    "browse": (registry.get_browse_provider_class, registry.available_browse_providers),
    "live_data": (registry.get_live_data_provider_class, registry.available_live_data_providers),
    "research": (registry.get_research_provider_class, registry.available_research_providers),
    "open_browser": (registry.get_browser_provider_class, registry.available_browser_providers),
}
_PINNED = tuple(PINNED_CALLS)
"""The methods a ``provider:mode`` id pins (search first), in the order a check without a tool tries them."""


def check_provider_id(provider_id: str | Provider, tool: str | None = None) -> None:
    """Raise if ``provider_id`` could not serve ``tool``, or any method when ``tool`` is ``None``; nothing is called.

    Any case is accepted, as the router accepts it: ``EXA`` is ``exa``, ``SEARCH`` is ``search``.

    Raises:
        ProviderNotFoundError: for a backend the method (or every method) does not know.
        InvalidParameterError: for an unknown ``tool``, a malformed id, an id naming another tool than ``tool``,
            or a mode its backend does not declare, or a mode on a method that takes none.
        ProviderNotInstalledError: when ``tool`` is given and its backend's extra is not installed, so its modes
            cannot be read. Without a tool such an id passes: the method that uses it says what to install.
    """
    text = provider_name(provider_id)
    tool = tool.lower() if tool is not None else None
    if tool is not None and tool not in TOOLS:
        raise InvalidParameterError("tool", MSG_TOOL_UNKNOWN.format(tool=tool), allowed=TOOLS)
    if not all(text.split(ID_SEP)):  # "exa:" would build, then fail on its first call
        raise InvalidParameterError(MODE_FIELD, MSG_ID_SHAPE.format(text=text))
    head = text.partition(ID_SEP)[0]
    named = head if ID_SEP in text and head in TOOLS else None
    if tool and named and named != tool:
        raise InvalidParameterError(MODE_FIELD, MSG_PIN_TOOL.format(text=text, tool=named, method=tool))
    if tool:
        _check_for(tool, text)
        return
    if named:
        with suppress(ProviderNotInstalledError):  # modes unread here; the method used names the extra to install
            _check_for(named, text, default=True)
        return
    backend, mode = split_pin(text)
    known = every_provider_name()
    if backend not in known:
        raise ProviderNotFoundError(text, known)
    if mode:
        _check_any(text, backend)


def _check_for(tool: str, text: str, *, default: bool = False) -> None:
    """``text`` resolved as ``tool`` resolves it; a method that pins nothing refuses a mode.

    A method that pins nothing takes no method prefix in a call (``research(provider="research:nimble")`` fails),
    but as a router's ``default`` it serves the id's backend.
    """
    backend, mode = split_pin(text)
    if mode and tool not in _PINNED:
        raise InvalidParameterError(MODE_FIELD, MSG_PIN_NONE.format(text=text, tools=tool))
    _CLASSES[tool][0](backend if default and tool not in _PINNED else text)


def _check_any(text: str, backend: str) -> None:
    """Pass when one pinning method that knows ``backend`` takes ``text``, else raise the first method's error."""
    tools = [tool for tool in _PINNED if backend in _CLASSES[tool][1]()]
    if not tools:
        raise InvalidParameterError(MODE_FIELD, MSG_PIN_NONE.format(text=text, tools=" or ".join(_PINNED)))
    errors: list[InvalidParameterError] = []
    for tool in tools:
        try:
            _CLASSES[tool][0](text)
        except ProviderNotInstalledError:
            return  # its modes can't be read here; the method that uses it names the extra to install
        except InvalidParameterError as exc:
            errors.append(exc)
        else:
            return
    raise errors[0]
