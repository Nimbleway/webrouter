"""Presets' definitions: an exact reference parsed, a definition checked against its methods, a file read.

Apart from :mod:`web_router.presets` so no file grows past the length rule;
the registry there is what a caller uses.
"""

from __future__ import annotations

import builtins
import copy
import importlib
import json
import math
from collections.abc import Callable, Iterator, Mapping
from functools import cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import TypeAdapter, ValidationError

from web_router.constants_presets import (
    MSG_PRESET_BAD_FILE,
    MSG_PRESET_BAD_REF,
    MSG_PRESET_BAD_REF_TYPE,
    MSG_PRESET_BAD_VALUE,
    MSG_PRESET_DEFINITION,
    MSG_PRESET_DEFINITION_IN,
    MSG_PRESET_INVALID,
    MSG_PRESET_NOT_FINITE,
    MSG_PRESET_NOT_JSON,
    MSG_PRESET_NULL_DEFAULT,
    MSG_PRESET_OVERRIDABLE_UNSET,
    MSG_PRESET_PARSE_POSITION,
    MSG_PRESET_SECTION_NOT_DECLARED,
    MSG_PRESET_UNKNOWN_FIELD,
    MSG_PRESET_UNKNOWN_PROVIDER,
    MSG_PRESET_YAML_INVALID,
    MSG_PRESET_YAML_MISSING,
    MSG_PRESET_YAML_UNBUILT,
    PRESET_FIELDS,
    PRESET_PROVIDER_FIELD,
    PRESET_REF_PATTERN,
    PRESET_REQUEST_MODELS,
    PRESET_YAML_SUFFIXES,
    YAML_TIMESTAMP_TAG,
)
from web_router.exceptions_presets import PresetError, PresetValidationError
from web_router.registry import (
    available_browse_providers,
    available_browser_providers,
    available_extract_providers,
    available_providers,
    available_research_providers,
)
from web_router.registry_answer import available_answer_providers
from web_router.types import provider_name
from web_router.types_meta import (
    PresetComponent,
)
from web_router.types_presets import Preset, PresetIssue

_AVAILABLE: dict[PresetComponent, Callable[[], list[str]]] = {
    "search": available_providers,
    "extract": available_extract_providers,
    "research": available_research_providers,
    "browse": available_browse_providers,
    "browser": available_browser_providers,
    "answer": available_answer_providers,
}
"""Each component's backends by name. ``templates`` has none: its ``provider`` is the method's, never a preset's."""


def parse_ref(ref: str) -> tuple[str, str]:
    """Split ``namespace.name@major.minor.patch`` into its id and version, or raise.

    Raises:
        PresetValidationError: for anything that is not an exact reference --
            a bare id, a range, ``latest``.
    """
    if not isinstance(ref, str):
        # Named by type, never repeated: a definition passed here by mistake can hold a key in provider_options.
        issue = PresetIssue(field="preset", message=MSG_PRESET_BAD_REF_TYPE.format(type=type(ref).__name__))
        raise PresetValidationError([issue], lead=MSG_PRESET_DEFINITION)
    match = PRESET_REF_PATTERN.fullmatch(ref)
    if match is None:
        raise PresetValidationError(
            [PresetIssue(field="preset", message=MSG_PRESET_BAD_REF)], lead=MSG_PRESET_DEFINITION
        )
    return match["id"], match["version"]


def _checked(definition: Preset | Mapping[str, Any], *, where: str | None = None) -> Preset:
    """``definition`` as a new :class:`Preset`, every section's settings checked against its method.

    Built from a deep copy, so nothing the caller still holds -- the model, the
    mapping or a dict nested in it -- is shared with the copy the registry keeps.
    """
    data = _plain(definition.model_dump() if isinstance(definition, Preset) else definition)
    lead = MSG_PRESET_DEFINITION_IN.format(where=where) if where else MSG_PRESET_DEFINITION
    non_finite = [PresetIssue(field=path or "preset", message=MSG_PRESET_NOT_FINITE) for path in _non_finite(data, "")]
    if non_finite:
        raise PresetValidationError(non_finite, lead=lead)
    try:
        preset = Preset.model_validate(data)
    except ValidationError as exc:
        issues = [
            PresetIssue(field=".".join(str(part) for part in error["loc"]) or "preset", message=error["msg"])
            for error in exc.errors()
        ]
        raise PresetValidationError(
            issues, lead=MSG_PRESET_DEFINITION_IN.format(where=where) if where else MSG_PRESET_DEFINITION
        ) from None
    issues = [
        issue
        for component, section in preset.components.items()
        for issue in _section_issues(preset, component, section.defaults, section.overridable)
    ]
    if issues:
        raise PresetValidationError(issues, lead=MSG_PRESET_INVALID.format(ref=preset.ref))
    return preset


def _non_finite(value: Any, path: str) -> Iterator[str]:
    """Where ``value`` holds a number JSON cannot: NaN or an infinity, which Python's JSON and YAML both read."""
    if isinstance(value, float) and not math.isfinite(value):
        yield path
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _non_finite(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (builtins.list, tuple)):
        for index, item in enumerate(value):
            yield from _non_finite(item, f"{path}.{index}" if path else str(index))


def _plain(value: Any) -> Any:
    """A deep copy of ``value`` with every mapping a dict, so a read-only one, which cannot be deep-copied, can too."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (builtins.list, tuple)):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)


def _section_issues(
    preset: Preset, component: PresetComponent, defaults: Mapping[str, Any], overridable: builtins.list[str]
) -> builtins.list[PresetIssue]:
    where = f"components.{component}"
    if component not in preset.applies_to:
        return [PresetIssue(field=where, message=MSG_PRESET_SECTION_NOT_DECLARED.format(component=component))]
    allowed = PRESET_FIELDS[component]
    issues: builtins.list[PresetIssue] = []
    for name, value in defaults.items():
        if name not in allowed:
            message = MSG_PRESET_UNKNOWN_FIELD.format(
                field=name, component=component, allowed=", ".join(sorted(allowed))
            )
            issues.append(PresetIssue(field=f"{where}.defaults.{name}", message=message))
            continue
        if value is None:
            # None means unset everywhere else; as a default it would be reported as applied and lock nothing in.
            issues.append(PresetIssue(field=f"{where}.defaults.{name}", message=MSG_PRESET_NULL_DEFAULT))
            continue
        try:
            _coerced(component, name, value)
        except PresetValidationError as exc:
            issues.extend(issue.model_copy(update={"field": f"{where}.defaults.{name}"}) for issue in exc.issues)
    issues.extend(
        PresetIssue(field=f"{where}.overridable", message=MSG_PRESET_OVERRIDABLE_UNSET.format(field=name))
        for name in overridable
        if name not in defaults
    )
    return issues


def _coerced(component: PresetComponent, name: str, value: Any) -> Any:
    """``value`` as the method's request model would hold it, or a field-level error."""
    if name == PRESET_PROVIDER_FIELD:
        available = _AVAILABLE[component]()
        if not isinstance(value, str) or provider_name(value) not in available:
            message = MSG_PRESET_UNKNOWN_PROVIDER.format(component=component, available=", ".join(available))
            raise PresetValidationError([PresetIssue(field=name, message=message)], lead=MSG_PRESET_DEFINITION)
        return provider_name(value)
    try:
        return _adapter(component, name).validate_python(value)
    except ValidationError as exc:
        error = "; ".join(item["msg"] for item in exc.errors())
        message = MSG_PRESET_BAD_VALUE.format(field=name, error=error)
        raise PresetValidationError([PresetIssue(field=name, message=message)], lead=MSG_PRESET_DEFINITION) from None


@cache
def _adapter(component: PresetComponent, name: str) -> TypeAdapter[Any]:
    # The FieldInfo carries the field's constraints, so `max_results=0` is refused here as the request would refuse it.
    info = PRESET_REQUEST_MODELS[component].model_fields[name]
    return TypeAdapter(Annotated[info.annotation, info])


def _changed(ref: str, template: str) -> PresetValidationError:
    issue = PresetIssue(field="version", message=template.format(ref=ref))
    return PresetValidationError([issue], lead=MSG_PRESET_INVALID.format(ref=ref))


def _copy(preset: Preset) -> Preset:
    return preset.model_copy(deep=True)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _read(file: Path) -> builtins.list[Any]:
    """The definitions in one file: a mapping is one, a list is several.

    A parse error is reported by its position alone, and without the parser's
    own error as its cause: both can quote the file, which can hold a key.
    """
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise PresetError(MSG_PRESET_BAD_FILE.format(path=file, error=exc)) from exc
    except UnicodeDecodeError as exc:
        raise PresetError(MSG_PRESET_BAD_FILE.format(path=file, error=exc)) from None
    if file.suffix not in PRESET_YAML_SUFFIXES:
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            where = MSG_PRESET_PARSE_POSITION.format(problem=exc.msg, line=exc.lineno, column=exc.colno)
            raise PresetError(MSG_PRESET_BAD_FILE.format(path=file, error=where)) from None
        return document if isinstance(document, builtins.list) else [document]
    try:
        # Imported by name, as the registry imports providers: PyYAML is optional, and its stubs are not
        # in the pre-commit mypy environment.
        yaml = importlib.import_module("yaml")
    except ImportError:
        raise PresetError(MSG_PRESET_YAML_MISSING.format(path=file)) from None
    try:
        document = yaml.load(text, Loader=_json_loader(yaml))
    except yaml.YAMLError as exc:
        raise PresetError(MSG_PRESET_BAD_FILE.format(path=file, error=_yaml_position(exc))) from None
    except Exception as exc:
        # A tag PyYAML could not build, such as a date that does not exist. Named by type: its text quotes the file.
        error = MSG_PRESET_YAML_UNBUILT.format(kind=type(exc).__name__)
        raise PresetError(MSG_PRESET_BAD_FILE.format(path=file, error=error)) from None
    try:
        json.dumps(document)
    except (TypeError, ValueError):
        raise PresetError(MSG_PRESET_BAD_FILE.format(path=file, error=MSG_PRESET_NOT_JSON)) from None
    return document if isinstance(document, builtins.list) else [document]


def _json_loader(yaml: Any) -> Any:
    """PyYAML's safe loader, less its timestamp resolver, so an unquoted date stays the string JSON would hold."""

    class Loader(yaml.SafeLoader):  # type: ignore[misc]
        pass

    Loader.yaml_implicit_resolvers = {
        first: [(tag, pattern) for tag, pattern in resolvers if tag != YAML_TIMESTAMP_TAG]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    return Loader


def _yaml_position(exc: Exception) -> str:
    """What kind of YAML error and where, never PyYAML's words, which can quote the file (an alias's name)."""
    problem = MSG_PRESET_YAML_INVALID.format(kind=type(exc).__name__)
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return problem
    return MSG_PRESET_PARSE_POSITION.format(problem=problem, line=mark.line + 1, column=mark.column + 1)
