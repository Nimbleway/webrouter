"""Presets' preflight: what :meth:`~web_router.PresetRegistry.validate` reports, read off the backend's class alone.

Apart from :mod:`web_router.presets` so no file grows past the length rule.
Nothing here sends anything or needs a key.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from web_router.constants_presets import (
    MSG_PRESET_BACKEND_NEEDS,
    MSG_PRESET_BACKEND_NEEDS_PARTNER,
    MSG_PRESET_BACKEND_REFUSES_OPTION,
    MSG_PRESET_BACKEND_REFUSES_VALUE,
    MSG_PRESET_BAD_ARGUMENT,
    MSG_PRESET_BAD_PROVIDER_ARGUMENT,
    MSG_PRESET_GIVEN_TWICE,
    MSG_PRESET_NO_PROVIDER,
    MSG_PRESET_TEMPLATES_PROVIDER,
    MSG_PRESET_UNKNOWN_BACKEND,
    MSG_PRESET_UNKNOWN_REQUEST_KEY,
    MSG_PRESET_UNSUPPORTED_FORMAT,
    MSG_PRESET_UNSUPPORTED_PARAMETER,
    PRESET_ARGUMENT,
    PRESET_CALL_ARGUMENTS,
    PRESET_FIELDS,
    PRESET_FORMATS,
    PRESET_NATIVE_INPUTS,
    PRESET_PROVIDER_FIELD,
    PRESET_REQUEST_MODELS,
)
from web_router.exceptions import (
    ProviderNotFoundError,
    WebRouterError,
)
from web_router.exceptions_presets import PresetError, PresetValidationError
from web_router.presets_layers import _PROVIDER_BASE
from web_router.presets_load import _adapter
from web_router.registry import (
    get_browse_provider_class,
    get_browser_provider_class,
    get_extract_provider_class,
    get_provider_class,
    get_research_provider_class,
    get_template_provider_class,
)
from web_router.registry_answer import default_answer_provider, get_answer_provider_class
from web_router.routing.contract import Budget
from web_router.types import provider_name
from web_router.types_meta import (
    PresetComponent,
)
from web_router.types_presets import Preset, PresetCheck, PresetIssue

if TYPE_CHECKING:
    from web_router.presets import PresetRegistry


_PROVIDER_CLASS: dict[PresetComponent, Callable[[str], type[Any]]] = {
    "search": get_provider_class,
    "extract": get_extract_provider_class,
    "templates": get_template_provider_class,
    "research": get_research_provider_class,
    "browse": get_browse_provider_class,
    "browser": get_browser_provider_class,
    "answer": get_answer_provider_class,
}


_CONDITIONS: tuple[tuple[str, str], ...] = (
    ("missing_parameters", MSG_PRESET_BACKEND_NEEDS),
    ("refused_values", MSG_PRESET_BACKEND_REFUSES_VALUE),
    ("unmet_requirements", MSG_PRESET_BACKEND_NEEDS_PARTNER),
    ("refused_options", MSG_PRESET_BACKEND_REFUSES_OPTION),
)
"""The class-level checks a backend may declare and the issue each reports. The call makes them before it bills."""


def _condition_issues(
    component: PresetComponent, backend: type[Any], provider: str, settings: Mapping[str, Any]
) -> builtins.list[PresetIssue]:
    """What the backend's class refuses beyond an option it does not map, through its own checks; never a value."""
    model = PRESET_REQUEST_MODELS[component]
    fields = {key: value for key, value in settings.items() if key in model.model_fields and value is not None}
    # Built without validation: the values were checked already, and the native inputs are not in a preflight.
    request = model.model_construct(**fields)
    issues: builtins.list[PresetIssue] = []
    for method, template in _CONDITIONS:
        check = getattr(backend, method, None)
        found = check(request) if check is not None else {}
        for option in sorted(found):
            partners = ", ".join(sorted(found[option])) if method == "unmet_requirements" else ""
            issues.append(
                PresetIssue(field=option, message=template.format(provider=provider, field=option, partners=partners))
            )
    return issues


def _native_inputs(
    component: PresetComponent, arguments: Mapping[str, Any]
) -> tuple[dict[str, Any], builtins.list[PresetIssue]]:
    """The native inputs the call was given, as its request would hold them, and each one it would refuse.

    Shaped first as the method shapes them -- one URL is a list of one, template
    inputs are a dict -- so a value the call takes is not reported. A bad one is
    named by field and never repeated; the backend's checks never read it. One
    left out is not checked, so a preset's check need not name them, but one
    given as ``None`` is, since the call refuses a required input set to ``None``.
    """
    native: dict[str, Any] = {}
    issues: builtins.list[PresetIssue] = []
    for name in sorted(PRESET_NATIVE_INPUTS[component]):
        if name not in arguments:
            continue
        value = arguments[name]
        if name == "urls" and isinstance(value, str):
            value = [value]
        elif name == "inputs" and isinstance(value, Mapping):
            value = dict(value)
        try:
            checked = _adapter(component, name).validate_python(value)
        except ValidationError as exc:
            error = "; ".join(item["msg"] for item in exc.errors())
            issues.append(PresetIssue(field=name, message=MSG_PRESET_BAD_ARGUMENT.format(field=name, error=error)))
            continue
        if checked is not None:
            native[name] = checked
    return native, issues


_CALL_ARGUMENT_TYPES: dict[str, TypeAdapter[Any]] = {
    "budget": TypeAdapter(Budget | None),
    "timeout": TypeAdapter(float | None),
}
"""What each call argument takes. Strict: the method reads a ``Budget``'s fields and never builds one from a dict."""


def _call_argument_issues(component: PresetComponent, arguments: Mapping[str, Any]) -> builtins.list[PresetIssue]:
    """The call arguments the method could not use, named by field and never repeated."""
    issues: builtins.list[PresetIssue] = []
    for name in sorted(PRESET_CALL_ARGUMENTS[component] & set(arguments)):
        try:
            _CALL_ARGUMENT_TYPES[name].validate_python(arguments[name], strict=True)
        except ValidationError as exc:
            error = "; ".join(item["msg"] for item in exc.errors())
            issues.append(PresetIssue(field=name, message=MSG_PRESET_BAD_ARGUMENT.format(field=name, error=error)))
    return issues


def _argument_issues(
    component: PresetComponent, explicit: Mapping[str, Any]
) -> tuple[dict[str, Any], builtins.list[PresetIssue]]:
    """The caller's own values as the request would hold them, and each it would refuse, named by field alone."""
    coerced: dict[str, Any] = {}
    issues: builtins.list[PresetIssue] = []
    for name, value in explicit.items():
        if value is None or name == PRESET_PROVIDER_FIELD:
            continue
        candidate = value.model_json_schema() if isinstance(value, type) and issubclass(value, BaseModel) else value
        try:
            coerced[name] = _adapter(component, name).validate_python(candidate)
        except ValidationError as exc:
            error = "; ".join(item["msg"] for item in exc.errors())
            issues.append(PresetIssue(field=name, message=MSG_PRESET_BAD_ARGUMENT.format(field=name, error=error)))
    return coerced, issues


def preflight(
    registry: PresetRegistry,
    component: PresetComponent,
    request: Mapping[str, Any] | None,
    preset: str | Preset | None,
) -> PresetCheck:
    """What :meth:`~web_router.PresetRegistry.validate` reports for one call: see there."""
    # Materialized once, so every check below reads the same values the call would.
    arguments: dict[str, Any] = {
        key: builtins.list(value) if isinstance(value, Iterator) else value for key, value in (request or {}).items()
    }
    given_twice = preset is not None and arguments.get(PRESET_ARGUMENT) is not None
    preset = preset if preset is not None else arguments.get(PRESET_ARGUMENT)
    arguments.pop(PRESET_ARGUMENT, None)
    settable = PRESET_FIELDS[component]
    takes = settable | PRESET_NATIVE_INPUTS[component] | PRESET_CALL_ARGUMENTS[component] | {PRESET_PROVIDER_FIELD}
    issues = [
        PresetIssue(field=key, message=MSG_PRESET_UNKNOWN_REQUEST_KEY.format(key=key, component=component))
        for key in sorted(arguments)
        if key not in takes
    ]
    if given_twice:
        issues.append(PresetIssue(field=PRESET_ARGUMENT, message=MSG_PRESET_GIVEN_TWICE))
    issues.extend(_call_argument_issues(component, arguments))
    explicit = {name: arguments.get(name) for name in settable}
    coerced, argument_issues = _argument_issues(component, explicit)
    issues.extend(argument_issues)
    try:
        resolution = registry.resolve(component, preset, **explicit)
    except PresetValidationError as exc:
        return PresetCheck(component=component, issues=[*issues, *exc.issues])
    except PresetError as exc:
        return PresetCheck(component=component, issues=[*issues, PresetIssue(field="preset", message=str(exc))])
    provider = resolution.provider if PRESET_PROVIDER_FIELD in settable else arguments.get(PRESET_PROVIDER_FIELD)
    # A backend's own checks can read a native input (Nimble's read `records`), so the preflight request carries
    # them beside the settings, validated; and they read values, so they run only once every value is valid.
    native, native_issues = _native_inputs(component, arguments)
    # The backend's checks read the values the call would send: "false" is sent as False, so read as False.
    own = {key: value for key, value in coerced.items() if key not in resolution.effective}
    name, backend_issues = _backend_issues(
        registry,
        component,
        provider,
        {**resolution.settings, **own, **native},
        conditions=not (argument_issues or native_issues),
    )
    return PresetCheck(
        component=component,
        provider=name,
        resolved=resolution.resolved,
        effective=resolution.effective,
        overridden=resolution.overridden,
        issues=[*issues, *native_issues, *backend_issues],
    )


def _backend_issues(
    registry: PresetRegistry,
    component: PresetComponent,
    provider: Any,
    settings: Mapping[str, Any],
    *,
    conditions: bool = True,
) -> tuple[str | None, builtins.list[PresetIssue]]:
    """What the backend the call would reach refuses, read off its class alone."""
    try:
        if isinstance(provider, str) or provider is None:
            name = _backend_name(registry, component, provider)
            backend = _PROVIDER_CLASS[component](name)
        elif isinstance(provider, _PROVIDER_BASE[component]):
            backend, name = type(provider), provider.name
        else:
            # The call would refuse it too: each method takes a backend built for it, never a class or another's.
            message = MSG_PRESET_BAD_PROVIDER_ARGUMENT.format(component=component)
            return None, [PresetIssue(field=PRESET_PROVIDER_FIELD, message=message)]
    except ProviderNotFoundError as exc:
        # The router's own default is a registered name and is shown; a name the caller passed that no
        # backend has is free text, and is not.
        if provider is None:
            message = MSG_PRESET_NO_PROVIDER.format(error=exc)
        else:
            message = MSG_PRESET_UNKNOWN_BACKEND.format(component=component, available=", ".join(exc.available))
        return None, [PresetIssue(field=PRESET_PROVIDER_FIELD, message=message)]
    except WebRouterError as exc:
        return None, [PresetIssue(field=PRESET_PROVIDER_FIELD, message=MSG_PRESET_NO_PROVIDER.format(error=exc))]
    model = PRESET_REQUEST_MODELS[component]
    set_here = {key for key in model.normalized_parameters() if settings.get(key) is not None}
    issues = [
        PresetIssue(field=key, message=MSG_PRESET_UNSUPPORTED_PARAMETER.format(provider=name, field=key))
        for key in sorted(set_here - backend.supported_parameters)
    ]
    requested = settings.get("format")
    formats = getattr(backend, "supported_formats", None)
    # Anything that is not a format is the caller's mistake, reported once by field and never repeated.
    if isinstance(requested, str) and requested in PRESET_FORMATS and formats is not None and requested not in formats:
        supported = ", ".join(sorted(formats))
        message = MSG_PRESET_UNSUPPORTED_FORMAT.format(provider=name, format=requested, supported=supported)
        issues.append(PresetIssue(field="format", message=message))
    if conditions:
        issues.extend(_condition_issues(component, backend, name, settings))
    return name, issues


def _backend_name(registry: PresetRegistry, component: PresetComponent, provider: str | None) -> str:
    if provider is not None:
        return provider_name(provider)
    if component == "answer":
        return default_answer_provider(registry.default_provider, registry._provider_config)
    if component == "templates":
        raise PresetError(MSG_PRESET_TEMPLATES_PROVIDER)
    return registry.default_provider
