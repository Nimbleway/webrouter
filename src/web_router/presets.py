"""Presets: saved settings a call names instead of repeating them.

A preset is a versioned definition -- ``acme.supplier-research@1.0.0`` -- holding
settings for one or more methods under each method's own argument names. A
router takes one of its own (``WebRouter(preset=...)``) and each call can name
one more (``preset=``). For every setting the call uses, in this order:

1. the argument the call passed,
2. the per-call preset,
3. the router's preset,
4. the method's own default.

A section may lock a setting by leaving it out of ``overridable``; replacing a
locked setting with a different value raises :class:`~web_router.PresetValidationError`
rather than letting either side win. Everything here runs before the backend is
called -- an unknown reference, a preset for another method, a setting that is
not the method's, a value of the wrong type -- so a preset problem never bills.

Definitions come only from what was loaded or passed in. Nothing is fetched,
nothing is upgraded on its own, and a version once loaded never changes: a
different definition under the same reference is refused.
"""

from __future__ import annotations

import builtins
import copy
import importlib
import json
import math
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cache
from os import PathLike
from pathlib import Path
from typing import Annotated, Any, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

from web_router.constants import DEFAULT_PROVIDER
from web_router.constants_presets import (
    MSG_PRESET_ALREADY_LOADED,
    MSG_PRESET_BACKEND_NEEDS,
    MSG_PRESET_BACKEND_NEEDS_PARTNER,
    MSG_PRESET_BACKEND_REFUSES_OPTION,
    MSG_PRESET_BACKEND_REFUSES_VALUE,
    MSG_PRESET_BAD_ARGUMENT,
    MSG_PRESET_BAD_FILE,
    MSG_PRESET_BAD_PROVIDER_ARGUMENT,
    MSG_PRESET_BAD_REF,
    MSG_PRESET_BAD_REF_TYPE,
    MSG_PRESET_BAD_VALUE,
    MSG_PRESET_CONFLICT,
    MSG_PRESET_DEFINED_TWICE,
    MSG_PRESET_DEFINITION,
    MSG_PRESET_DEFINITION_IN,
    MSG_PRESET_EMPTY_DIRECTORY,
    MSG_PRESET_GIVEN_TWICE,
    MSG_PRESET_INVALID,
    MSG_PRESET_LOCKED,
    MSG_PRESET_NO_PROVIDER,
    MSG_PRESET_NOT_FINITE,
    MSG_PRESET_NOT_JSON,
    MSG_PRESET_NULL_DEFAULT,
    MSG_PRESET_OVERRIDABLE_UNSET,
    MSG_PRESET_PARSE_POSITION,
    MSG_PRESET_SECTION_NOT_DECLARED,
    MSG_PRESET_SOURCE_CALL,
    MSG_PRESET_SOURCE_PRESET,
    MSG_PRESET_SUPPLIED,
    MSG_PRESET_TEMPLATES_PROVIDER,
    MSG_PRESET_UNKNOWN_BACKEND,
    MSG_PRESET_UNKNOWN_FIELD,
    MSG_PRESET_UNKNOWN_PROVIDER,
    MSG_PRESET_UNKNOWN_REQUEST_KEY,
    MSG_PRESET_UNSUPPORTED_FORMAT,
    MSG_PRESET_UNSUPPORTED_PARAMETER,
    MSG_PRESET_YAML_MISSING,
    MSG_PRESET_YAML_UNBUILT,
    PRESET_ARGUMENT,
    PRESET_CALL_ARGUMENTS,
    PRESET_CASE_BLIND,
    PRESET_FIELDS,
    PRESET_FILE_SUFFIXES,
    PRESET_FORMATS,
    PRESET_NATIVE_INPUTS,
    PRESET_PROVIDER_FIELD,
    PRESET_REF_PATTERN,
    PRESET_REQUEST_MODELS,
    PRESET_WITHHELD_FIELDS,
    PRESET_YAML_SUFFIXES,
    YAML_TIMESTAMP_TAG,
)
from web_router.exceptions import (
    PresetCompatibilityError,
    PresetError,
    PresetNotFoundError,
    PresetValidationError,
    ProviderNotFoundError,
    UnsupportedFormatError,
    UnsupportedParameterError,
    WebRouterError,
)
from web_router.providers.answer import AnswerProvider
from web_router.providers.base import (
    BrowseProvider,
    BrowserProvider,
    ExtractProvider,
    ResearchProvider,
    SearchProvider,
    TemplateProvider,
)
from web_router.registry import (
    available_browse_providers,
    available_browser_providers,
    available_extract_providers,
    available_providers,
    available_research_providers,
    get_browse_provider_class,
    get_browser_provider_class,
    get_extract_provider_class,
    get_provider_class,
    get_research_provider_class,
    get_template_provider_class,
)
from web_router.registry_answer import available_answer_providers, default_answer_provider, get_answer_provider_class
from web_router.types import (
    PresetComponent,
    PresetMeta,
    PresetSource,
    PresetValidation,
    PresetValidationState,
    ResolvedPreset,
    ResponseMeta,
    provider_name,
)
from web_router.types_presets import Preset, PresetCheck, PresetIssue

__all__ = ["PresetRegistry", "PresetResolution", "parse_ref"]

_ModelT = TypeVar("_ModelT", bound=BaseModel)

_AVAILABLE: dict[PresetComponent, Callable[[], list[str]]] = {
    "search": available_providers,
    "extract": available_extract_providers,
    "research": available_research_providers,
    "browse": available_browse_providers,
    "browser": available_browser_providers,
    "answer": available_answer_providers,
}
"""Each component's backends by name. ``templates`` has none: its ``provider`` is the method's, never a preset's."""

_PROVIDER_BASE: dict[PresetComponent, type[Any]] = {
    "search": SearchProvider,
    "extract": ExtractProvider,
    "templates": TemplateProvider,
    "research": ResearchProvider,
    "browse": BrowseProvider,
    "browser": BrowserProvider,
    "answer": AnswerProvider,
}
"""Each method's backend class: a provider object passed to it must be an instance, as its router getter checks."""

_PROVIDER_CLASS: dict[PresetComponent, Callable[[str], type[Any]]] = {
    "search": get_provider_class,
    "extract": get_extract_provider_class,
    "templates": get_template_provider_class,
    "research": get_research_provider_class,
    "browse": get_browse_provider_class,
    "browser": get_browser_provider_class,
    "answer": get_answer_provider_class,
}


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


@dataclass(frozen=True)
class PresetResolution:
    """What a call runs with once its presets are applied: every setting, and the metadata to return.

    :attr:`settings` holds every setting the call names, ``None`` for one left
    unset, so it can be passed back to the method's request builder as it is.
    """

    component: PresetComponent
    settings: dict[str, Any]
    resolved: list[ResolvedPreset] = field(default_factory=list)
    effective: dict[str, Any] = field(default_factory=dict)
    overridden: list[str] = field(default_factory=list)

    @property
    def provider(self) -> Any:
        """The backend to call: the argument, a preset's or ``None`` for the router's default."""
        return self.settings.get(PRESET_PROVIDER_FIELD)

    @property
    def options(self) -> dict[str, Any]:
        """Every setting but ``provider``, for the method's request builder."""
        return {name: value for name, value in self.settings.items() if name != PRESET_PROVIDER_FIELD}

    def meta(self, state: PresetValidationState) -> ResponseMeta | None:
        """The metadata a response carries, or ``None`` when no preset applied."""
        if not self.resolved:
            return None
        preset = PresetMeta(
            component=self.component,
            resolved=self.resolved,
            effective=self.effective,
            overridden=self.overridden,
            validation=PresetValidation(state=state),
        )
        return ResponseMeta(preset=preset)

    def stamp(self, response: _ModelT) -> _ModelT:
        """``response`` with this call's metadata on it; untouched when no preset applied."""
        meta = self.meta("passed")
        return response if meta is None else response.model_copy(update={"meta": meta})

    @contextmanager
    def explained(self) -> Iterator[None]:
        """Name the presets when the backend refuses a setting one of them supplied and the call never passed.

        The error keeps its type and its fields, so a caller handling it as
        before still does; only its message gains the preset, since the advice
        it carries is written for a caller who set the option themselves.
        """
        try:
            yield
        except (UnsupportedParameterError, UnsupportedFormatError) as exc:
            if isinstance(exc, UnsupportedParameterError):
                named = sorted(set(exc.parameters) & set(self.effective))
            else:
                named = ["format"] if "format" in self.effective else []
            if named:
                refs = ", ".join(entry.ref for entry in self.resolved)
                note = MSG_PRESET_SUPPLIED.format(names=", ".join(named), refs=refs)
                exc.args = (f"{exc} {note}", *exc.args[1:])
            raise


class PresetRegistry:
    """The presets one router can use, and the router's own default.

    Every router has one, as ``router.presets``. Load definitions into it with
    :meth:`load`, or pass a :class:`~web_router.Preset` straight to ``preset=``,
    which adds it. Lookups need an exact ``id@version``.
    """

    def __init__(
        self,
        *,
        client: str | Preset | None = None,
        default_provider: str = DEFAULT_PROVIDER,
        provider_config: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        """
        Args:
            client: The router's own preset, applied to every call it applies
                to. A reference is checked for form now and looked up on each
                call, so it can be loaded after the router is built.
            default_provider: The backend a call with no ``provider`` goes to,
                which :meth:`validate` checks settings against.
            provider_config: The router's ``provider_config``, which decides
                the backend an unnamed ``answer()`` goes to, so :meth:`validate`
                picks the one the call would.
        """
        self._presets: dict[str, Preset] = {}
        self._lock = threading.Lock()
        self.default_provider = default_provider
        self._provider_config: Mapping[str, Mapping[str, Any]] = provider_config or {}
        self.client: str | None = None if client is None else self._reference(client)

    def load(self, path: str | PathLike[str]) -> builtins.list[Preset]:
        """Load every preset in a JSON or YAML file, or in every such file directly inside a directory.

        A file holds one definition or a list of them. All of them are checked
        before any is added, so a bad file leaves the registry as it was.
        Reloading a definition that is already loaded is a no-op. YAML needs
        PyYAML; JSON needs nothing.

        Raises:
            PresetValidationError: for a definition that is malformed, holds a
                setting its method does not take or a value of the wrong type,
                or differs from another under the same reference, loaded
                before or in this same call.
            PresetError: for a file that cannot be read or parsed, or a
                directory with no preset files in it.
        """
        source = Path(path)
        if source.is_dir():
            files = sorted(item for item in source.iterdir() if item.is_file() and item.suffix in PRESET_FILE_SUFFIXES)
            if not files:
                raise PresetError(MSG_PRESET_EMPTY_DIRECTORY.format(path=source))
        else:
            files = [source]
        batch: dict[str, Preset] = {}
        for preset in (_checked(data, where=str(file)) for file in files for data in _read(file)):
            if batch.setdefault(preset.ref, preset) != preset:
                raise _changed(preset.ref, MSG_PRESET_DEFINED_TWICE)
        with self._lock:
            for preset in batch.values():
                self._refuse_change(preset)
            self._presets.update(batch)
        return [_copy(preset) for preset in batch.values()]

    def add(self, preset: Preset | Mapping[str, Any]) -> Preset:
        """Add one definition, a :class:`~web_router.Preset` or the mapping a file would hold, and return it.

        The registry keeps its own copy, so changing what was passed in, or
        what comes back, changes nothing a call uses.

        Raises:
            PresetValidationError: as for :meth:`load`, or for a different
                definition under a reference that is already loaded.
        """
        checked = self._stored(_checked(preset))
        return _copy(checked)

    def get(self, ref: str) -> Preset:
        """A copy of the preset loaded under the exact reference ``ref``.

        Raises:
            PresetValidationError: for a reference that is not exact.
            PresetNotFoundError: for one nothing is loaded under, naming the
                versions of the same id that are.
        """
        return _copy(self._get(ref))

    def _get(self, ref: str) -> Preset:
        preset_id, _ = parse_ref(ref)
        with self._lock:
            preset = self._presets.get(ref)
            if preset is None:
                versions = [known.version for known in self._presets.values() if known.id == preset_id]
                raise PresetNotFoundError(ref, versions)
            return preset

    def _stored(self, preset: Preset) -> Preset:
        with self._lock:
            self._refuse_change(preset)
            return self._presets.setdefault(preset.ref, preset)

    def resolve(self, component: PresetComponent, preset: str | Preset | None, **explicit: Any) -> PresetResolution:
        """Apply the per-call ``preset`` and the router's own to one call's arguments.

        ``explicit`` is every setting the method takes, ``None`` where the call
        left it unset. A per-call preset that does not apply to ``component``
        raises; the router's own simply does not apply.

        Raises:
            PresetNotFoundError: for a preset that is not loaded.
            PresetCompatibilityError: for a per-call preset for another method.
            PresetValidationError: for a locked setting replaced with another value.
        """
        layers: builtins.list[tuple[PresetSource, Preset]] = []
        if preset is not None:
            call = self._stored(_checked(preset)) if isinstance(preset, Preset) else self._get(preset)
            if component not in call.applies_to:
                raise PresetCompatibilityError(call.ref, component, call.applies_to)
            layers.append(("request", call))
        if self.client is not None:
            client = self._get(self.client)
            if component in client.applies_to:
                layers.append(("client", client))
        if not layers:
            return PresetResolution(component, dict(explicit))

        supplied, issues = _layered(component, layers)
        settings = dict(explicit)
        effective: dict[str, Any] = {}
        overridden: builtins.list[str] = []
        for name, entry in supplied.items():
            given = explicit.get(name)
            if given is None:
                # A copy: a request, or a provider handed it, must never reach back into the registry.
                settings[name] = copy.deepcopy(entry.value)
                effective[name] = _shown(name, entry.value)
                continue
            # What was compared is what is sent: validating the caller's value can use up a one-shot iterable.
            settings[name] = normalized = _normalized(component, name, given)
            if _same(component, name, normalized, entry.value):
                continue
            if entry.locker is not None:
                issues.append(_locked(name, entry.locker, source=MSG_PRESET_SOURCE_CALL))
            else:
                overridden.append(name)
        if issues:
            raise PresetValidationError(issues, lead=MSG_PRESET_CONFLICT.format(component=component))
        resolved = [ResolvedPreset(source=source, id=layer.id, version=layer.version) for source, layer in layers]
        return PresetResolution(component, settings, resolved, effective, sorted(overridden))

    def validate(
        self,
        component: PresetComponent,
        request: Mapping[str, Any] | None = None,
        *,
        preset: str | Preset | None = None,
    ) -> PresetCheck:
        """Check a call against its presets and its backend without making it.

        ``request`` is the call's arguments as the method takes them --
        ``{"query": ..., "max_results": 3}`` for a search. The report gives
        the presets that applied and the settings they supplied, and lists as
        issues every preset problem, any argument the method does not take,
        each setting the backend does not map and a format it cannot return.
        It also runs the checks the backend's class makes before a call bills:
        an option it needs left unset, a value it maps but cannot serve, an
        option missing its partner and an option it refuses outright. Only the
        class is read: nothing is sent and no key is needed.
        """
        # Materialized once, so every check below reads the same values the call would.
        arguments: dict[str, Any] = {
            key: builtins.list(value) if isinstance(value, Iterator) else value
            for key, value in (request or {}).items()
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
        explicit = {name: arguments.get(name) for name in settable}
        argument_issues = _argument_issues(component, explicit)
        issues.extend(argument_issues)
        try:
            resolution = self.resolve(component, preset, **explicit)
        except PresetValidationError as exc:
            return PresetCheck(component=component, issues=[*issues, *exc.issues])
        except PresetError as exc:
            return PresetCheck(component=component, issues=[*issues, PresetIssue(field="preset", message=str(exc))])
        provider = resolution.provider if PRESET_PROVIDER_FIELD in settable else arguments.get(PRESET_PROVIDER_FIELD)
        # A backend's own checks can read a native input (Nimble's read `records`), so the preflight request carries
        # them beside the settings; and they read values, so they run only once every value is one the request takes.
        native = {key: arguments[key] for key in PRESET_NATIVE_INPUTS[component] if arguments.get(key) is not None}
        name, backend_issues = self._backend_issues(
            component, provider, {**resolution.settings, **native}, conditions=not argument_issues
        )
        return PresetCheck(
            component=component,
            provider=name,
            resolved=resolution.resolved,
            effective=resolution.effective,
            overridden=resolution.overridden,
            issues=[*issues, *backend_issues],
        )

    def _backend_issues(
        self, component: PresetComponent, provider: Any, settings: Mapping[str, Any], *, conditions: bool = True
    ) -> tuple[str | None, builtins.list[PresetIssue]]:
        """What the backend the call would reach refuses, read off its class alone."""
        try:
            if isinstance(provider, str) or provider is None:
                name = self._backend_name(component, provider)
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
        if requested in PRESET_FORMATS and formats is not None and requested not in formats:
            supported = ", ".join(sorted(formats))
            message = MSG_PRESET_UNSUPPORTED_FORMAT.format(provider=name, format=requested, supported=supported)
            issues.append(PresetIssue(field="format", message=message))
        if conditions:
            issues.extend(_condition_issues(component, backend, name, settings))
        return name, issues

    def _backend_name(self, component: PresetComponent, provider: str | None) -> str:
        if provider is not None:
            return provider_name(provider)
        if component == "answer":
            return default_answer_provider(self.default_provider, self._provider_config)
        if component == "templates":
            raise PresetError(MSG_PRESET_TEMPLATES_PROVIDER)
        return self.default_provider

    def _reference(self, preset: str | Preset) -> str:
        if isinstance(preset, Preset):
            return self.add(preset).ref
        parse_ref(preset)
        return preset

    def _refuse_change(self, preset: Preset) -> None:
        known = self._presets.get(preset.ref)
        if known is not None and known != preset:
            raise _changed(preset.ref, MSG_PRESET_ALREADY_LOADED)

    def list(self, component: PresetComponent | None = None) -> builtins.list[Preset]:
        """A copy of every loaded preset, by reference, or only those that apply to ``component``."""
        with self._lock:
            presets = sorted(self._presets.values(), key=lambda preset: (preset.id, _version_key(preset.version)))
        return [_copy(preset) for preset in presets if component is None or component in preset.applies_to]

    def __len__(self) -> int:
        return len(self._presets)

    def __repr__(self) -> str:
        return f"<PresetRegistry presets={len(self)} client={self.client!r}>"


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


def _layered(
    component: PresetComponent, layers: builtins.list[tuple[PresetSource, Preset]]
) -> tuple[dict[str, _Supplied], builtins.list[PresetIssue]]:
    """Every setting the presets supply for ``component``, the lock holding each, and the conflicts between layers.

    Lowest precedence first, so each layer is laid over the one beneath it.
    """
    supplied: dict[str, _Supplied] = {}
    issues: builtins.list[PresetIssue] = []
    for _, layer in reversed(layers):
        for name, raw in layer.section(component).defaults.items():
            issue = _laid(component, supplied, layer, name, _coerced(component, name, raw))
            if issue is not None:
                issues.append(issue)
    return supplied, issues


def _laid(
    component: PresetComponent, supplied: dict[str, _Supplied], layer: Preset, name: str, value: Any
) -> PresetIssue | None:
    """Lay ``layer``'s value for ``name`` over what is beneath it, or return the conflict with a lock there.

    The lock is carried up with the value: a layer above that repeats a locked value must not release it.
    """
    beneath = supplied.get(name)
    locker = beneath.locker if beneath is not None else None
    if beneath is not None and locker is not None and not _same(component, name, value, beneath.value):
        return _locked(name, locker, source=MSG_PRESET_SOURCE_PRESET.format(ref=layer.ref))
    supplied[name] = _Supplied(value, layer if layer.section(component).locks(name) else locker)
    return None


@dataclass(frozen=True)
class _Supplied:
    """A setting a preset supplied, and the preset that locks it, if any does, at this layer or below."""

    value: Any
    locker: Preset | None


def _same(component: PresetComponent, name: str, one: Any, other: Any) -> bool:
    """Whether two values of a setting send the same request: equal, or equal but for case where it takes either."""
    if name == PRESET_PROVIDER_FIELD:
        # A backend built for this method stands for its name: the router uses the instance, as the name's own.
        base = _PROVIDER_BASE[component]
        one, other = (value.name if isinstance(value, base) else value for value in (one, other))
    if name in PRESET_CASE_BLIND[component] and isinstance(one, str) and isinstance(other, str):
        return one.upper() == other.upper()
    return bool(one == other)


def _normalized(component: PresetComponent, name: str, given: Any) -> Any:
    """``given`` as the preset's value would be held, so the same setting written another way is not a conflict.

    A value the field refuses is returned as it came: the request builder
    refuses it on its own, in its own words.
    """
    if name == PRESET_PROVIDER_FIELD:
        return provider_name(given) if isinstance(given, str) else given
    if isinstance(given, type) and issubclass(given, BaseModel):
        given = given.model_json_schema()
    if isinstance(given, Iterator):
        given = builtins.list(given)
    try:
        return _adapter(component, name).validate_python(given)
    except ValidationError:
        return given


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


def _argument_issues(component: PresetComponent, explicit: Mapping[str, Any]) -> builtins.list[PresetIssue]:
    """The caller's own values the request would refuse, named by field and never repeated."""
    issues: builtins.list[PresetIssue] = []
    for name, value in explicit.items():
        if value is None or name == PRESET_PROVIDER_FIELD:
            continue
        candidate = value.model_json_schema() if isinstance(value, type) and issubclass(value, BaseModel) else value
        try:
            _adapter(component, name).validate_python(candidate)
        except ValidationError as exc:
            error = "; ".join(item["msg"] for item in exc.errors())
            issues.append(PresetIssue(field=name, message=MSG_PRESET_BAD_ARGUMENT.format(field=name, error=error)))
    return issues


def _shown(name: str, value: Any) -> Any:
    """``value`` as metadata and messages may show it: by its keys alone for a setting that can hold a key."""
    if name in PRESET_WITHHELD_FIELDS:
        return sorted(value)
    return to_jsonable_python(value)


def _locked(name: str, locker: Preset, *, source: str) -> PresetIssue:
    return PresetIssue(field=name, message=MSG_PRESET_LOCKED.format(ref=locker.ref, field=name, source=source))


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
    """What went wrong in a YAML file and where, without the snippet of the file PyYAML quotes beside it."""
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or type(exc).__name__
    if mark is None:
        return str(problem)
    return MSG_PRESET_PARSE_POSITION.format(problem=problem, line=mark.line + 1, column=mark.column + 1)
