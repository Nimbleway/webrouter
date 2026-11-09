"""Each tool's params: the router method's own arguments, typed and checked as the method checks them.

A tool's params are derived from the request builder its router method calls
(:mod:`web_router.client_requests`) plus the arguments the method reads itself
-- ``provider`` everywhere, and ``timeout`` for the two jobs. Where a builder
argument is a field of the method's request model, the field's own type and
constraints are used, so ``max_results=0`` is refused in a preset exactly as in
a call. Only ``budget`` is left out: it caps one call's spend and is not
something a saved setting holds.

A preset holds only optional arguments: the per-call input the method takes
positionally -- the query, the URLs, the task, the template -- is always the
call's. Every param is optional in a preset, and a call that sets one itself
overrides it.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal, get_args, get_type_hints

from pydantic import BaseModel, ConfigDict, ValidationError, create_model
from pydantic_core import InitErrorDetails, PydanticCustomError
from typing_extensions import Unpack

from web_router.client_requests import (
    build_answer_request,
    build_browse_request,
    build_browser_request,
    build_extract_request,
    build_research_request,
    build_search_request,
    build_template_request,
)
from web_router.constants_presets import (
    MSG_PRESET_NUL,
    MSG_PRESET_UNKNOWN_PARAM,
    PRESET_PARSE_PROBLEM,
    PRESET_PROBLEM,
    PRESET_PROBLEM_ROOT,
    PRESET_PROBLEM_SEPARATOR,
    PRESET_REDACTED,
    PRESET_SECRET_PARAMS,
)
from web_router.presets.limits import LIMITS, Text
from web_router.types import (
    BrowseRequest,
    BrowserRequest,
    ExtractRequest,
    NormalizedRequest,
    ResearchRequest,
    SearchRequest,
    TemplateRequest,
)
from web_router.types_answer import AnswerRequest
from web_router.types_options import SearchOptions

PresetTool = Literal["search", "extract", "answer", "research", "browse", "run_template", "open_browser"]
"""The router methods a preset can run, each named as the method is."""

PRESET_TOOLS: tuple[str, ...] = get_args(PresetTool)


@dataclass(frozen=True)
class _ToolSpec:
    builder: Callable[..., NormalizedRequest]
    request: type[NormalizedRequest]
    timeout: bool = False
    # Builder arguments a preset takes in the method's shape rather than the request's: one URL or a list.
    as_the_method_takes: frozenset[str] = field(default_factory=frozenset)
    # Optional arguments that are still the call's input, as the managed presets hold: rows to enrich.
    per_call: frozenset[str] = field(default_factory=frozenset)


_SPECS: dict[str, _ToolSpec] = {
    "search": _ToolSpec(build_search_request, SearchRequest),
    "extract": _ToolSpec(build_extract_request, ExtractRequest, as_the_method_takes=frozenset({"urls"})),
    "answer": _ToolSpec(build_answer_request, AnswerRequest),
    "research": _ToolSpec(build_research_request, ResearchRequest, timeout=True, per_call=frozenset({"records"})),
    "browse": _ToolSpec(build_browse_request, BrowseRequest, timeout=True),
    "run_template": _ToolSpec(build_template_request, TemplateRequest),
    "open_browser": _ToolSpec(build_browser_request, BrowserRequest),
}


def _annotation(tool: str, spec: _ToolSpec, name: str, hints: dict[str, Any]) -> Any:
    if (tool, name) in LIMITS:
        return LIMITS[tool, name]
    model_field = spec.request.model_fields.get(name)
    if model_field is None or name in spec.as_the_method_takes:
        return hints[name]
    return model_field.rebuild_annotation()


@cache
def _arguments(tool: str) -> tuple[tuple[str, Any], ...]:
    """``(name, type)`` for each optional argument the tool's method takes, in the method's order."""
    spec = _SPECS[tool]
    # `Unpack` and the options' TypedDict are imported for type checking only, where a builder takes them.
    hints = get_type_hints(spec.builder, localns={"Unpack": Unpack, "SearchOptions": SearchOptions})
    names: list[str] = []
    for name, parameter in inspect.signature(spec.builder).parameters.items():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            # `**options: Unpack[SearchOptions]`: every option the TypedDict names, a request field each.
            names.extend(get_type_hints(get_args(hints[name])[0]))
        # A positional argument with no default is the call's own input; the builders' keyword-only arguments
        # all have a default on the method itself.
        elif name not in spec.per_call and not (
            parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD and parameter.default is inspect.Parameter.empty
        ):
            names.append(name)
    arguments = [(name, _annotation(tool, spec, name, hints)) for name in names]
    if tool != "run_template":  # a template's name means something to one vendor only: the call names it
        arguments.insert(0, ("provider", Text))
    if spec.timeout:
        arguments.append(("timeout", float | None))
    return tuple(arguments)


@cache
def _model(tool: str) -> type[BaseModel]:
    fields: dict[str, Any] = {name: (annotation | None, None) for name, annotation in _arguments(tool)}
    return create_model(tool, __config__=ConfigDict(extra="forbid"), **fields)


def tool_params(tool: str) -> list[str]:
    """Every param ``tool`` takes, in the order its router method takes them."""
    return [name for name, _ in _arguments(tool)]


def _known(tool: str, params: Any) -> None:
    """Refuse each key ``tool`` does not take, by name and with what it takes: pydantic's own error names neither."""
    takes = tool_params(tool)
    unknown = [key for key in params if key not in takes] if isinstance(params, dict) else []
    if not unknown:
        return
    context = {"a": "an" if tool[0] in "aeiou" else "a", "tool": tool, "takes": ", ".join(takes)}
    raise ValidationError.from_exception_data(
        tool,
        [
            InitErrorDetails(
                type=PydanticCustomError("unknown_param", MSG_PRESET_UNKNOWN_PARAM, {**context, "key": key}),
                loc=(key,),
                input=params[key],
            )
            for key in unknown
        ],
    )


def preset_params(tool: str, params: Any) -> dict[str, Any]:
    """``params`` checked as ``tool``'s method checks them, in the JSON form a store keeps.

    Raises:
        pydantic.ValidationError: a param ``tool`` does not take, or one of the wrong type or out of range --
            the method's own range, or the tighter one :mod:`web_router.presets.limits` holds a preset to.
    """
    _known(tool, params)
    # None is unset: every store reads an unset param back as absent, so one saved as None would not round-trip.
    kept = _model(tool).model_validate(params).model_dump(mode="json", exclude_none=True)
    _without_nul(tool, kept)
    return kept


def _without_nul(tool: str, params: dict[str, Any]) -> None:
    """Refuse a NUL in any text param or list of text, which Postgres text cannot keep; a schema keeps its own."""
    bad = [
        key
        for key, value in params.items()
        if (isinstance(value, str) and "\x00" in value)
        or (isinstance(value, list) and any(isinstance(item, str) and "\x00" in item for item in value))
    ]
    if bad:
        raise ValidationError.from_exception_data(
            tool,
            [
                InitErrorDetails(type=PydanticCustomError("nul", MSG_PRESET_NUL), loc=(key,), input=params[key])
                for key in bad
            ],
        )


def shown(params: dict[str, Any]) -> dict[str, Any]:
    """``params`` as safe to show -- in a response, a listing, a tool result: ``provider_options`` by key alone.

    Its values are a vendor's own fields and may carry a credential, which a
    preset's reader or a response's has no use for.
    """
    return {
        key: {option: PRESET_REDACTED for option in value}
        if key in PRESET_SECRET_PARAMS and isinstance(value, dict)
        else value
        for key, value in params.items()
    }


def problems(exc: BaseException) -> str:
    """What is wrong with an unreadable preset, without the values: each field's location and the kind of error.

    A stored value can be a secret pasted in the wrong place; the message goes to logs and tool results.
    """
    if isinstance(exc, ValidationError):
        return PRESET_PROBLEM_SEPARATOR.join(
            PRESET_PROBLEM.format(where=".".join(map(str, error["loc"])) or PRESET_PROBLEM_ROOT, kind=error["type"])
            for error in exc.errors()
        )
    if isinstance(exc, json.JSONDecodeError):
        return PRESET_PARSE_PROBLEM.format(msg=exc.msg, line=exc.lineno, column=exc.colno)
    return type(exc).__name__
