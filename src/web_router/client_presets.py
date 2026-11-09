"""``preset=``: one decorator that lets every router method run with a saved preset's arguments.

Each method takes ``preset: str | None = None`` and is wrapped in
:func:`with_preset`. A call naming a preset is the method called with the
preset's params, the call's own arguments on top, through the same public path
-- request builder, validation, routing and wrapping -- a call without one
takes; the response then carries :class:`~web_router.PresetApplied` in its
``preset`` field. A preset of another method, or a name the router's store does
not hold, raises before anything is sent.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar, cast

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from web_router.exceptions_presets import PresetNotFoundError, PresetOfAnotherToolError
from web_router.presets.params import shown, tool_params
from web_router.types_preset import PresetApplied

P = ParamSpec("P")
R = TypeVar("R")

PRESET_ARGUMENT = "preset"
PRESET_FIELD = "preset"


def _merged(
    tool: str, signature: inspect.Signature, args: Any, kwargs: Any
) -> tuple[Any, dict[str, Any], PresetApplied] | None:
    given = dict(signature.bind_partial(*args, **kwargs).arguments)
    self = given.pop("self")
    name = given.pop(PRESET_ARGUMENT, None)
    if name is None:
        return None
    preset = self.presets.get(name)
    if preset is None:
        raise PresetNotFoundError(name)
    if preset.tool != tool:
        raise PresetOfAnotherToolError(name, preset.tool)
    # `submit_research` and `submit_browse` take no `timeout`: a research preset's wait is for the methods that wait.
    arguments = {key: value for key, value in preset.params.items() if key in signature.parameters}
    arguments.update(given)
    optional = set(tool_params(tool))
    echo = {key: value for key, value in arguments.items() if key in optional}
    return self, arguments, PresetApplied(name=name, params=shown(to_jsonable_python(echo, fallback=str)))


def _echoed(result: Any, applied: PresetApplied) -> Any:
    # A job or a browser handle has no `preset` field: it is returned as it is.
    if isinstance(result, BaseModel) and PRESET_FIELD in type(result).model_fields:
        return result.model_copy(update={PRESET_FIELD: applied})
    return result


def with_preset(tool: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Wrap one router method of ``tool`` so its ``preset=`` argument applies that preset.

    The method's own ``preset`` parameter is only its signature: the wrapper
    consumes it. ``submit_research`` and ``submit_browse`` use ``research`` and
    ``browse`` presets.
    """

    def decorate(method: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(method)

        if inspect.iscoroutinefunction(method):

            @functools.wraps(method)
            async def awaited(*args: Any, **kwargs: Any) -> Any:
                merged = _merged(tool, signature, args, kwargs)
                if merged is None:
                    return await method(*args, **kwargs)
                self, arguments, applied = merged
                return _echoed(await method(self, **arguments), applied)

            return cast(Callable[P, R], awaited)

        @functools.wraps(method)
        def called(*args: Any, **kwargs: Any) -> Any:
            merged = _merged(tool, signature, args, kwargs)
            if merged is None:
                return method(*args, **kwargs)
            self, arguments, applied = merged
            return _echoed(method(self, **arguments), applied)

        return cast(Callable[P, R], called)

    return decorate
