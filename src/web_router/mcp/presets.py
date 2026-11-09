"""Presets over MCP: ``list_presets``, ``get_preset`` and ``create_preset``, and ``preset`` on the method tools.

A preset is resolved here, in the server, rather than by the library's own
``preset=``: its params join the tool's arguments before the server applies its
own rules to them -- the session timeout it lowers, the budget it charges, the
options it refuses -- so a preset can do nothing over MCP that the same call
spelled out could not. The result then says which preset ran and with what.
The store is the router's, picked by ``WEB_ROUTER_PRESETS`` at startup.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, TypeVar, cast

from pydantic_core import to_jsonable_python

from web_router.constants_mcp_presets import (
    ENV_MCP_PRESETS,
    MCP_ARG_PRESET,
    MCP_MSG_PRESET_NOT_OVER_MCP,
    MCP_MSG_PRESETS_EMPTY,
    MCP_PRESET_METHOD_OF,
    MCP_PRESET_SERVER_WAITS,
    MCP_PRESETS_URL_MARK,
    MCP_TOOL_CREATE_PRESET,
    MCP_TOOL_GET_PRESET,
    MCP_TOOL_LIST_PRESETS,
)
from web_router.exceptions import ConfigurationError
from web_router.exceptions_presets import PresetNotFoundError
from web_router.mcp.schema import argument_names
from web_router.presets import FilePresetStore, InMemoryPresetStore, Preset, PresetStore, tool_params
from web_router.presets.params import shown
from web_router.presets.store import preset_for
from web_router.presets_sql import SqlPresetStore
from web_router.types_preset import PresetApplied

if TYPE_CHECKING:
    from web_router.mcp.tools import WebRouterTools

_R = TypeVar("_R")
_RAN: ContextVar[tuple[str, str, dict[str, Any]] | None] = ContextVar("web_router_mcp_preset_ran", default=None)
"""The preset the current call ran with: its name, its method and the arguments sent, set by :func:`expanded`."""
_JOB_TOOLS = frozenset({"research", "browse"})
_MCP_TOOL_OF = {method: tool for tool, method in MCP_PRESET_METHOD_OF.items()}


@contextmanager
def fresh_preset() -> Iterator[None]:
    """One tool call's scope for the preset it ran with: nothing a failed call recorded reaches the next one."""
    token = _RAN.set(None)
    try:
        yield
    finally:
        _RAN.reset(token)


def store_from(value: str | None) -> PresetStore:
    """The store ``WEB_ROUTER_PRESETS`` names: a database URL, a directory, or, unset, the server's memory.

    Raises:
        ConfigurationError: for the variable set empty.
        PresetStoreOutdatedError: for a database whose schema is behind this version's.
    """
    if value is None:
        return InMemoryPresetStore()
    spec = value.strip()
    if not spec:
        raise ConfigurationError(MCP_MSG_PRESETS_EMPTY.format(env_var=ENV_MCP_PRESETS))
    return SqlPresetStore(spec) if MCP_PRESETS_URL_MARK in spec else FilePresetStore(spec)


def _preset(store: PresetStore, tool: str, name: str) -> Preset:
    return preset_for(store, name, MCP_PRESET_METHOD_OF[tool])


def _off_mcp(name: str, tool: str, params: Mapping[str, Any], takes: Collection[str]) -> None:
    """Refuse a preset setting a param ``tool`` does not take over MCP -- ``provider_options``, ``profile``."""
    waits = MCP_PRESET_SERVER_WAITS if tool in _JOB_TOOLS else frozenset()
    refused = sorted(key for key in params if key not in takes and key not in waits)
    if refused:
        raise ValueError(
            MCP_MSG_PRESET_NOT_OVER_MCP.format(
                name=name, keys=", ".join(refused), tool=tool, them="them" if len(refused) > 1 else "it"
            )
        )


def expanded(store: PresetStore, tool: str, arguments: Mapping[str, Any], takes: Collection[str]) -> dict[str, Any]:
    """The library call's keyword arguments: those ``takes`` names that are set, the named preset's params under
    them, and ``preset`` taken out.

    Raises:
        PresetNotFoundError, PresetOfAnotherToolError: as the library's ``preset=`` does.
        ValueError: for a preset setting a param the tool does not take over MCP -- ``provider_options``, say.
    """
    _RAN.set(None)
    kwargs = {name: arguments[name] for name in takes if arguments.get(name) is not None}
    name = kwargs.pop(MCP_ARG_PRESET, None)
    if name is None or tool not in MCP_PRESET_METHOD_OF:
        return kwargs
    preset = _preset(store, tool, str(name))
    _off_mcp(str(name), tool, preset.params, takes)
    waits = MCP_PRESET_SERVER_WAITS if tool in _JOB_TOOLS else frozenset()
    params = {key: value for key, value in preset.params.items() if key not in waits}
    merged = {**params, **kwargs}
    # The very dict the tool sends: a limit the tool lowers in it afterwards is what the echo shows.
    _RAN.set((str(name), MCP_PRESET_METHOD_OF[tool], merged))
    return merged


def echoed(result: _R) -> _R:
    """The shaped result, saying which preset ran and the params it ran with, when the call named one.

    Read from the arguments :func:`expanded` handed the tool, as the tool left them, never from the store
    again: what the echo shows is what ran.
    """
    ran = _RAN.get()
    _RAN.set(None)
    if ran is None or not isinstance(result, dict):
        return result
    name, method, sent = ran
    params = {key: sent[key] for key in tool_params(method) if key in sent}
    applied = PresetApplied(name=name, params=shown(to_jsonable_python(params, fallback=str)))
    return cast(_R, {**result, MCP_ARG_PRESET: applied.model_dump()})


async def list_presets(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"presets": [_safe(preset) for preset in tools.router.presets.list()]}


async def get_preset(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    name = str(tools.arguments(MCP_TOOL_GET_PRESET, arguments)["name"])
    preset = tools.router.presets.get(name)
    if preset is None:
        raise PresetNotFoundError(name)
    return _safe(preset)


async def create_preset(tools: WebRouterTools, arguments: Mapping[str, Any]) -> dict[str, Any]:
    preset = Preset.model_validate(tools.arguments(MCP_TOOL_CREATE_PRESET, arguments))
    # Refused now rather than on every run: a preset made over MCP is one MCP can run.
    method_tool = _MCP_TOOL_OF[preset.tool]
    _off_mcp(preset.name, method_tool, preset.params, argument_names(method_tool))
    tools.router.presets.create(preset)
    return _safe(preset)


def _safe(preset: Preset) -> dict[str, Any]:
    """The preset as a tool returns it: ``provider_options`` by key alone, since a stored value may be a key."""
    return {**preset.model_dump(exclude_none=True), "params": shown(preset.params)}


PRESET_TOOLS: dict[str, Callable[[WebRouterTools, Mapping[str, Any]], Awaitable[dict[str, Any]]]] = {
    MCP_TOOL_LIST_PRESETS: list_presets,
    MCP_TOOL_GET_PRESET: get_preset,
    MCP_TOOL_CREATE_PRESET: create_preset,
}
"""The preset tools, each a coroutine of the session's tools and the call's arguments, as ``ROUTING_TOOLS`` are."""
