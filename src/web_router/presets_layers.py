"""Presets' precedence: the layers laid over one another and over a call's own arguments.

Apart from :mod:`web_router.presets` so no file grows past the length rule.
:class:`PresetResolution` is what one call runs with once its presets apply.
"""

from __future__ import annotations

import builtins
import copy
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from web_router.constants_presets import (
    MSG_PRESET_CONFLICT,
    MSG_PRESET_LOCKED,
    MSG_PRESET_SOURCE_CALL,
    MSG_PRESET_SOURCE_PRESET,
    MSG_PRESET_SUPPLIED,
    PRESET_CASE_BLIND,
    PRESET_PRICED_FIELDS,
    PRESET_PROVIDER_FIELD,
    PRESET_WITHHELD_FIELDS,
)
from web_router.exceptions import (
    UnsupportedFormatError,
    UnsupportedParameterError,
)
from web_router.exceptions_budget import BudgetExceededError
from web_router.exceptions_presets import PresetValidationError
from web_router.presets_load import _adapter, _coerced
from web_router.providers.answer import AnswerProvider
from web_router.providers.base import (
    BrowseProvider,
    BrowserProvider,
    ExtractProvider,
    ResearchProvider,
    SearchProvider,
    TemplateProvider,
)
from web_router.types import provider_name
from web_router.types_meta import (
    PresetComponent,
    PresetMeta,
    PresetSource,
    PresetValidation,
    PresetValidationState,
    ResolvedPreset,
    ResponseMeta,
)
from web_router.types_presets import Preset, PresetIssue

_ModelT = TypeVar("_ModelT", bound=BaseModel)


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
        """Name the presets when the backend or the budget refuses a call shaped by a setting one of them supplied.

        The error keeps its type and its fields, so a caller handling it as
        before still does; only its message gains the preset, since the advice
        it carries is written for a caller who set the option themselves. A
        budget's refusal names the settings the presets supplied among those it
        prices the call by (``PRESET_PRICED_FIELDS``): ``provider_options``
        alone leaves a price unknown.
        """
        try:
            yield
        except (UnsupportedParameterError, UnsupportedFormatError, BudgetExceededError) as exc:
            if isinstance(exc, UnsupportedParameterError):
                named = sorted(set(exc.parameters) & set(self.effective))
            elif isinstance(exc, BudgetExceededError):
                named = sorted(set(self.effective) & PRESET_PRICED_FIELDS)
            else:
                named = ["format"] if "format" in self.effective else []
            if named:
                refs = ", ".join(entry.ref for entry in self.resolved)
                note = MSG_PRESET_SUPPLIED.format(names=", ".join(named), refs=refs)
                exc.args = (f"{exc} {note}", *exc.args[1:])
            raise


def _layered(
    component: PresetComponent, layers: builtins.list[tuple[PresetSource, Preset]]
) -> tuple[dict[str, _Supplied], builtins.list[PresetIssue]]:
    """Every setting the presets supply for ``component`` with the lock holding each, then the layers' conflicts.

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


def _shown(name: str, value: Any) -> Any:
    """``value`` as metadata and messages may show it: by its keys alone for a setting that can hold a key."""
    if name in PRESET_WITHHELD_FIELDS:
        return sorted(value)
    return to_jsonable_python(value)


def _locked(name: str, locker: Preset, *, source: str) -> PresetIssue:
    return PresetIssue(field=name, message=MSG_PRESET_LOCKED.format(ref=locker.ref, field=name, source=source))


def applied(
    component: PresetComponent, layers: builtins.list[tuple[PresetSource, Preset]], explicit: Mapping[str, Any]
) -> PresetResolution:
    """``explicit``, the call's own arguments, with ``layers`` beneath them: what the call runs with, and its metadata.

    ``layers`` are the presets that apply, the per-call one first. An argument
    the call passed wins over a preset's value, unless a preset locks it.

    Raises:
        PresetValidationError: for a locked setting replaced with another value.
    """
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
