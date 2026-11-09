"""Presets' component table, field tables, reference format and messages.

Kept apart so ``constants.py`` stops growing; import them from here by name. Imports
``types``, ``types_answer``, ``types_presets`` and ``types_meta`` only: AGENTS.md lets a
``constants_*`` module import the models, and ``types_presets`` imports only ``types_meta``, a leaf.
"""

from __future__ import annotations

import re
from typing import get_args

from web_router.types import (
    BrowseRequest,
    BrowserRequest,
    ExtractFormat,
    ExtractRequest,
    NormalizedRequest,
    ResearchRequest,
    SearchRequest,
    TemplateRequest,
)
from web_router.types_answer import AnswerRequest
from web_router.types_meta import PresetComponent
from web_router.types_presets import PRESET_ID_PATTERN, PRESET_VERSION_PATTERN

__all__ = [
    "MSG_PRESET_ALREADY_LOADED",
    "MSG_PRESET_BACKEND_NEEDS",
    "MSG_PRESET_BACKEND_NEEDS_PARTNER",
    "MSG_PRESET_BACKEND_REFUSES_OPTION",
    "MSG_PRESET_BACKEND_REFUSES_VALUE",
    "MSG_PRESET_BAD_ARGUMENT",
    "MSG_PRESET_BAD_FILE",
    "MSG_PRESET_BAD_PROVIDER_ARGUMENT",
    "MSG_PRESET_BAD_REF",
    "MSG_PRESET_BAD_REF_TYPE",
    "MSG_PRESET_BAD_VALUE",
    "MSG_PRESET_CONFLICT",
    "MSG_PRESET_DEFINED_TWICE",
    "MSG_PRESET_DEFINITION",
    "MSG_PRESET_DEFINITION_IN",
    "MSG_PRESET_EMPTY_DIRECTORY",
    "MSG_PRESET_GIVEN_TWICE",
    "MSG_PRESET_INCOMPATIBLE",
    "MSG_PRESET_INVALID",
    "MSG_PRESET_KEY_NOT_STRING",
    "MSG_PRESET_LOCKED",
    "MSG_PRESET_NOT_FINITE",
    "MSG_PRESET_NOT_FOUND",
    "MSG_PRESET_NOT_JSON",
    "MSG_PRESET_NO_PROVIDER",
    "MSG_PRESET_NULL_DEFAULT",
    "MSG_PRESET_OVERRIDABLE_UNSET",
    "MSG_PRESET_PARSE_POSITION",
    "MSG_PRESET_SECTION_NOT_DECLARED",
    "MSG_PRESET_SOURCE_CALL",
    "MSG_PRESET_SOURCE_PRESET",
    "MSG_PRESET_SUPPLIED",
    "MSG_PRESET_TEMPLATES_PROVIDER",
    "MSG_PRESET_TOOLS_EXTRACT_SPLIT",
    "MSG_PRESET_UNKNOWN_BACKEND",
    "MSG_PRESET_UNKNOWN_FIELD",
    "MSG_PRESET_UNKNOWN_PROVIDER",
    "MSG_PRESET_UNKNOWN_REQUEST_KEY",
    "MSG_PRESET_UNSUPPORTED_FORMAT",
    "MSG_PRESET_UNSUPPORTED_PARAMETER",
    "MSG_PRESET_VERSIONS",
    "MSG_PRESET_YAML_INVALID",
    "MSG_PRESET_YAML_MISSING",
    "MSG_PRESET_YAML_UNBUILT",
    "PRESET_ARGUMENT",
    "PRESET_CALL_ARGUMENTS",
    "PRESET_CASE_BLIND",
    "PRESET_FIELDS",
    "PRESET_FILE_SUFFIXES",
    "PRESET_FORMATS",
    "PRESET_NATIVE_INPUTS",
    "PRESET_PRICED_FIELDS",
    "PRESET_PROVIDER_FIELD",
    "PRESET_REF_PATTERN",
    "PRESET_REQUEST_MODELS",
    "PRESET_WITHHELD_FIELDS",
    "PRESET_YAML_SUFFIXES",
    "YAML_BOOL_PATTERN",
    "YAML_BOOL_TAG",
    "YAML_TIMESTAMP_TAG",
]

PRESET_REF_PATTERN = re.compile(rf"^(?P<id>{PRESET_ID_PATTERN})@(?P<version>{PRESET_VERSION_PATTERN})$")
"""What ``preset=`` takes as a string: ``namespace.name@major.minor.patch``."""

PRESET_ARGUMENT = "preset"
"""The argument every method takes a preset by; :meth:`~web_router.PresetRegistry.validate` reads it from a request."""

YAML_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"
"""The resolver presets leave out of PyYAML's safe loader, so a YAML file holds the strings a JSON one would."""

YAML_BOOL_TAG = "tag:yaml.org,2002:bool"
YAML_BOOL_PATTERN = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
"""The only plain scalars a preset's YAML reads as booleans: YAML 1.2's, not 1.1's ``yes``, ``no``, ``on`` or ``off``.

A free-form value such as a country code ``NO`` must reach the backend as written.
"""

PRESET_PROVIDER_FIELD = "provider"
"""The one setting that is not a request field: which backend serves the call, as ``provider=`` on the method."""

PRESET_REQUEST_MODELS: dict[PresetComponent, type[NormalizedRequest]] = {
    "search": SearchRequest,
    "extract": ExtractRequest,
    "templates": TemplateRequest,
    "research": ResearchRequest,
    "browse": BrowseRequest,
    "browser": BrowserRequest,
    "answer": AnswerRequest,
}
"""Each component's request model, which a preset's values are validated against."""

PRESET_NATIVE_INPUTS: dict[PresetComponent, frozenset[str]] = {
    "search": frozenset({"query"}),
    "extract": frozenset({"urls"}),
    # `template` and `provider` stay required on run_template, so a preset
    # cannot supply them without changing the method for callers who pass none.
    "templates": frozenset({"template", "inputs"}),
    # `records` are rows the caller already holds: data for one run, not configuration.
    "research": frozenset({"query", "records"}),
    "browse": frozenset({"task"}),
    "browser": frozenset(),
    "answer": frozenset({"query"}),
}
"""The inputs that change on every call. They stay on the method and a preset cannot hold them."""

_TEMPLATE_UNPRESETTABLE = frozenset({PRESET_PROVIDER_FIELD})


def _fields(component: PresetComponent) -> frozenset[str]:
    model = PRESET_REQUEST_MODELS[component]
    fields = (frozenset(model.model_fields) - PRESET_NATIVE_INPUTS[component]) | {PRESET_PROVIDER_FIELD}
    return fields - _TEMPLATE_UNPRESETTABLE if component == "templates" else fields


PRESET_FIELDS: dict[PresetComponent, frozenset[str]] = {
    component: _fields(component) for component in PRESET_REQUEST_MODELS
}
"""Every setting each component's section may hold: its request fields less the native inputs, plus ``provider``.

Derived from the request models, so a field added to one is presettable without
a second edit. A research or browse call's ``timeout`` is how long the caller
waits, not part of the request, and is not here.
"""

_WAITS = frozenset({"research", "browse"})
_BUDGETED = frozenset({"search", "extract", "answer", "research", "browse"})

PRESET_CALL_ARGUMENTS: dict[PresetComponent, frozenset[str]] = {
    component: frozenset({"timeout"} if component in _WAITS else ())
    | frozenset({"budget"} if component in _BUDGETED else ())
    for component in PRESET_REQUEST_MODELS
}
"""Arguments a method takes that are about the call, not the request, so no preset holds them.

A research or browse ``timeout`` is how long to wait, and a ``budget`` caps
what the call may spend and how long it may take.
"""

PRESET_CASE_BLIND: dict[PresetComponent, frozenset[str]] = {
    component: frozenset({"proxy_country"}) if component == "browser" else frozenset()
    for component in PRESET_REQUEST_MODELS
}
"""Settings a request takes in either case, so a lock compares them without it: ``BrowserRequest.proxy_country``."""

PRESET_FORMATS: frozenset[str] = frozenset(get_args(ExtractFormat))
"""Every format there is. :meth:`~web_router.PresetRegistry.validate` checks a backend's formats only for these."""

PRESET_PRICED_FIELDS = frozenset({PRESET_PROVIDER_FIELD, "provider_options", "search_depth", "max_results", "effort"})
"""The settings a budget prices a call by (:mod:`web_router.routing.pricing`): the backend, its mode, its size.

A budget's refusal names those of them a preset supplied, and no other.
"""

PRESET_WITHHELD_FIELDS = frozenset({"provider_options"})
"""Settings metadata shows by their keys alone and errors never show by value: a backend's options can hold a key."""

PRESET_FILE_SUFFIXES = frozenset({".json", ".yaml", ".yml"})
"""What :meth:`~web_router.PresetRegistry.load` reads from a directory. Anything else there is left alone."""

PRESET_YAML_SUFFIXES = frozenset({".yaml", ".yml"})

MSG_PRESET_BAD_REF = (
    "preset= takes an exact reference, namespace.name@major.minor.patch -- for example "
    "'acme.supplier-research@1.0.0' -- and the string given is not one."
)
"""Never repeats the string: anything that is not a reference may be a key or file text passed by mistake."""
MSG_PRESET_BAD_REF_TYPE = "preset= takes a reference string or a Preset, not a {type}"
MSG_PRESET_NOT_FOUND = "No preset {ref!r} is loaded. {versions}Load its definition with router.presets.load(path)."
MSG_PRESET_VERSIONS = "Loaded versions of {id!r}: {versions}. "
MSG_PRESET_INCOMPATIBLE = "Preset {ref!r} does not apply to {component}; it applies to {applies_to}."
MSG_PRESET_INVALID = "Preset {ref!r} is invalid"
MSG_PRESET_DEFINITION = "A preset definition is invalid"
MSG_PRESET_DEFINITION_IN = "A preset definition in {where} is invalid"
MSG_PRESET_CONFLICT = "This {component} call conflicts with its presets"
MSG_PRESET_ALREADY_LOADED = (
    "Preset {ref!r} is already loaded with a different definition. A published version never changes: "
    "save the new definition under a new version."
)
MSG_PRESET_DEFINED_TWICE = (
    "Preset {ref!r} is defined more than once, with different definitions, in what was loaded. A version names "
    "one definition: give the other a new version."
)
MSG_PRESET_GIVEN_TWICE = "the preset is given both as an argument and in the request"
MSG_PRESET_BAD_ARGUMENT = "the call's {field} is not valid: {error}"
MSG_PRESET_YAML_UNBUILT = "a value PyYAML could not build ({kind})"
MSG_PRESET_YAML_INVALID = "YAML that does not parse ({kind})"
"""Names PyYAML's error class, never its text, which can repeat a value from the file."""
MSG_PRESET_NOT_JSON = (
    "it holds a value JSON cannot represent, such as a set or bytes; a preset holds only what JSON can"
)
MSG_PRESET_BAD_FILE = "Could not read presets from {path}: {error}"
MSG_PRESET_EMPTY_DIRECTORY = "No preset files (.json, .yaml, .yml) in {path}."
MSG_PRESET_YAML_MISSING = (
    "Reading {path} needs PyYAML, which is not installed. Install it (pip install pyyaml) or write the preset as JSON."
)
MSG_PRESET_SECTION_NOT_DECLARED = "has settings for {component} but does not list it in applies_to"
MSG_PRESET_UNKNOWN_FIELD = "{field!r} is not a {component} setting a preset can hold; it can hold {allowed}"
MSG_PRESET_BAD_VALUE = "not a valid {field}: {error}"
"""Names the field, never the value: a definition can hold a key by mistake, and errors get logged."""
MSG_PRESET_KEY_NOT_STRING = (
    "it holds a key that is not a string, which JSON cannot; in YAML, quote a key such as a number, true, false or null"
)
MSG_PRESET_NOT_FINITE = "NaN and infinity are not numbers JSON can hold; a preset holds only what JSON can"
MSG_PRESET_NULL_DEFAULT = "a default of null sets nothing; leave the setting out instead"
MSG_PRESET_PARSE_POSITION = "{problem} at line {line}, column {column}"
MSG_PRESET_SOURCE_PRESET = "preset {ref!r}"
MSG_PRESET_SOURCE_CALL = "the call"
MSG_PRESET_SUPPLIED = "{names}: set by this call's presets ({refs}), not by its arguments."
MSG_PRESET_OVERRIDABLE_UNSET = "{field!r} is listed as overridable but the preset sets no default for it"
MSG_PRESET_UNKNOWN_PROVIDER = "the provider named does not serve {component}; it is served by {available}"
MSG_PRESET_LOCKED = "preset {ref!r} sets {field} and does not let it be overridden, but {source} set another value"
"""Names the field, never either value: a preset's or a caller's can hold a key, and errors get logged."""
MSG_PRESET_UNKNOWN_REQUEST_KEY = "{key!r} is not an argument {component} takes"
MSG_PRESET_TOOLS_EXTRACT_SPLIT = (
    "The extract tool's routers disagree: one router's preset names an extract backend and the other's names none, "
    "so no backend fits both. Pass extract_provider= to name one for both."
)
MSG_PRESET_TEMPLATES_PROVIDER = "run_template has no default backend; name one with provider"
MSG_PRESET_BAD_PROVIDER_ARGUMENT = "provider takes a backend's name or an instance of a {component} backend"
MSG_PRESET_UNKNOWN_BACKEND = "no {component} backend has the name given; these do: {available}"
"""Never repeats the name: a string that is no backend's name is free text, and can be a key passed by mistake."""
MSG_PRESET_BACKEND_NEEDS = "{provider!r} needs {field} set alongside what this call sets"
MSG_PRESET_BACKEND_REFUSES_VALUE = "{provider!r} maps {field} but cannot serve the value asked for"
MSG_PRESET_BACKEND_NEEDS_PARTNER = "{provider!r} serves {field} only with {partners} set"
MSG_PRESET_BACKEND_REFUSES_OPTION = "{provider!r} refuses {field} outright"
MSG_PRESET_NO_PROVIDER = "no backend to check against: {error}"
MSG_PRESET_UNSUPPORTED_PARAMETER = "{provider!r} does not map {field}, so the call would be refused before it is sent"
MSG_PRESET_UNSUPPORTED_FORMAT = "{provider!r} cannot return format {format!r}; it returns {supported}"
