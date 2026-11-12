"""A request model's validation error, as the one-line :class:`InvalidParameterError` a caller branches on."""

from __future__ import annotations

import functools
import types
from collections.abc import Callable
from typing import Any, Literal, ParamSpec, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from web_router.exceptions_typed import InvalidParameterError

_P = ParamSpec("_P")
_R = TypeVar("_R")


def refused_as_parameter(model: type[BaseModel]) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Make a builder of ``model`` raise :class:`InvalidParameterError` where pydantic would raise its own error.

    The request types keep pydantic's error, which a caller building one
    directly expects; only the routers' builders translate it, so a method call
    raises a :class:`~web_router.WebRouterError` like everything else it raises.
    """

    def decorate(build: Callable[_P, _R]) -> Callable[_P, _R]:
        @functools.wraps(build)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            try:
                return build(*args, **kwargs)
            except ValidationError as exc:
                raise invalid_parameter(model, exc) from None

        return wrapper

    return decorate


def invalid_parameter(model: type[BaseModel], exc: ValidationError) -> InvalidParameterError:
    """The first error of ``exc`` as one :class:`InvalidParameterError`, its field's literal values as ``allowed``."""
    error = exc.errors(include_url=False)[0]
    location = [str(part) for part in error["loc"]]
    field = ".".join(location) or model.__name__
    reason = error["msg"].rstrip(".") + "."
    top = model.model_fields.get(location[0]) if location else None
    return InvalidParameterError(field, reason, allowed=_literals(top.annotation) if top is not None else None)


def _literals(annotation: Any) -> list[Any] | None:
    """The values of a ``Literal`` annotation, looking through ``Optional``; ``None`` for any other type."""
    if get_origin(annotation) is Literal:
        return list(get_args(annotation))
    if get_origin(annotation) in (Union, types.UnionType):
        for arm in get_args(annotation):
            if (values := _literals(arm)) is not None:
                return values
    return None
