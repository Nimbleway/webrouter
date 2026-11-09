"""Presets: loading a definition, checking it against its methods and reading a file.

Every router method takes ``preset=``, and these run each one over the in-memory
fakes in conftest, so nothing here touches the network or needs a key. A fake's
``requests`` list is the evidence that a call did, or did not, reach the backend.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.presets_helpers import REF, definition, searched
from web_router import (
    PresetError,
    PresetNotFoundError,
    PresetRegistry,
    PresetValidationError,
    WebRouter,
)
from web_router.constants_presets import PRESET_FIELDS
from web_router.types import NORMALIZED_PARAMETERS


def test_presets_load_from_json_and_yaml_files_and_directories(tmp_path: Path) -> None:
    (tmp_path / "search.json").write_text(json.dumps(definition("acme.search")))
    (tmp_path / "extract.yaml").write_text(
        "spec_version: 1\nid: acme.extract\nversion: 2.1.0\napplies_to: [extract]\n"
        "components:\n  extract:\n    defaults: {format: text}\n"
    )
    (tmp_path / "notes.txt").write_text("not a preset")
    registry = PresetRegistry()

    loaded = registry.load(tmp_path)

    assert [preset.ref for preset in loaded] == ["acme.extract@2.1.0", "acme.search@1.0.0"]
    assert registry.get("acme.extract@2.1.0").section("extract").defaults == {"format": "text"}
    assert [preset.ref for preset in registry.list(component="search")] == ["acme.search@1.0.0"]


def test_a_file_can_hold_several_definitions(tmp_path: Path) -> None:
    path = tmp_path / "presets.json"
    path.write_text(json.dumps([definition(version="1.0.0"), definition(version="1.1.0")]))
    registry = PresetRegistry()

    registry.load(path)

    assert [preset.version for preset in registry.list()] == ["1.0.0", "1.1.0"]


def test_a_lookup_needs_an_exact_version_and_names_the_ones_loaded() -> None:
    registry = PresetRegistry()
    registry.add(definition(version="1.0.0"))
    registry.add(definition(version="1.2.0"))

    with pytest.raises(PresetValidationError, match=re.escape("namespace.name@major.minor.patch")):
        registry.get("acme.research")
    with pytest.raises(PresetNotFoundError) as caught:
        registry.get("acme.research@2.0.0")
    assert caught.value.versions == ["1.0.0", "1.2.0"]


def test_a_loaded_version_never_changes() -> None:
    registry = PresetRegistry()
    registry.add(definition())
    registry.add(definition())  # the same definition again is a no-op

    with pytest.raises(PresetValidationError, match="new version"):
        registry.add(definition(components={"search": {"defaults": {"max_results": 9}}}))
    assert registry.get("acme.research@1.0.0").section("search").defaults == {"max_results": 5}


def test_a_bad_file_leaves_the_registry_as_it_was(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps(definition("acme.good")))
    (tmp_path / "b.json").write_text(
        json.dumps(definition("acme.bad", components={"search": {"defaults": {"nope": 1}}}))
    )
    registry = PresetRegistry()

    with pytest.raises(PresetValidationError):
        registry.load(tmp_path)
    assert len(registry) == 0


def test_yaml_without_pyyaml_names_the_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "preset.yaml"
    path.write_text("spec_version: 1\n")
    monkeypatch.setitem(sys.modules, "yaml", None)

    with pytest.raises(PresetError, match="pyyaml"):
        PresetRegistry().load(path)


@pytest.mark.parametrize(
    ("components", "field"),
    [
        ({"search": {"defaults": {"query": "fixed"}}}, "components.search.defaults.query"),
        ({"research": {"defaults": {"timeout": 60}}}, "components.research.defaults.timeout"),
        ({"research": {"defaults": {"records": []}}}, "components.research.defaults.records"),
        ({"templates": {"defaults": {"template": "books_pdp"}}}, "components.templates.defaults.template"),
        ({"search": {"defaults": {"max_results": 0}}}, "components.search.defaults.max_results"),
        ({"extract": {"defaults": {"format": "pdf"}}}, "components.extract.defaults.format"),
        ({"search": {"defaults": {"provider": "oxylabs"}}}, "components.search.defaults.provider"),
        ({"search": {"defaults": {}, "overridable": ["max_results"]}}, "components.search.overridable"),
    ],
)
def test_a_setting_its_method_cannot_take_fails_at_load(components: dict[str, Any], field: str) -> None:
    with pytest.raises(PresetValidationError) as caught:
        PresetRegistry().add(definition(components=components))
    assert [issue.field for issue in caught.value.issues] == [field]


def test_a_section_must_be_listed_in_applies_to() -> None:
    malformed = definition()
    malformed["applies_to"] = ["extract"]
    with pytest.raises(PresetValidationError) as caught:
        PresetRegistry().add(malformed)
    assert caught.value.issues[0].field == "components.search"


@pytest.mark.parametrize(
    "change",
    [{"spec_version": 2}, {"id": "nonamespace"}, {"version": "latest"}, {"shared": {"policy_refs": []}}],
)
def test_the_manifest_refuses_what_this_library_cannot_honor(change: dict[str, Any]) -> None:
    with pytest.raises(PresetValidationError):
        PresetRegistry().add({**definition(), **change})


def test_every_component_can_hold_provider_options_and_none_holds_its_native_input() -> None:
    assert all("provider_options" in fields for fields in PRESET_FIELDS.values())
    assert "query" not in PRESET_FIELDS["search"]
    assert "urls" not in PRESET_FIELDS["extract"]
    assert "task" not in PRESET_FIELDS["browse"]
    assert PRESET_FIELDS["search"] == NORMALIZED_PARAMETERS | {"provider", "provider_options"}


def test_one_load_refuses_two_definitions_under_one_reference(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps(definition("acme.same")))
    (tmp_path / "b.json").write_text(
        json.dumps(definition("acme.same", components={"search": {"defaults": {"max_results": 9}}}))
    )
    registry = PresetRegistry()

    with pytest.raises(PresetValidationError, match="more than once"):
        registry.load(tmp_path)
    assert len(registry) == 0


def test_a_reference_with_a_trailing_newline_is_refused() -> None:
    with pytest.raises(PresetValidationError):
        WebRouter("auto", preset="acme.research@1.0.0\n")


def test_a_null_default_is_refused_at_load() -> None:
    with pytest.raises(PresetValidationError, match="null"):
        PresetRegistry().add(definition(components={"search": {"defaults": {"country": None}}}))


@pytest.mark.parametrize(
    "text", ["provider_options: {api_key: sk-SECRET-123: oops}", "provider_options: {api_key: *sk-SECRET-123}"]
)
def test_a_yaml_parse_error_never_quotes_the_file(tmp_path: Path, text: str) -> None:
    path = tmp_path / "preset.yaml"
    path.write_text(f"spec_version: 1\n{text}\n")

    with pytest.raises(PresetError) as caught:
        PresetRegistry().load(path)

    assert "sk-SECRET-123" not in str(caught.value), "PyYAML's problem text repeats an alias's name"
    assert caught.value.__cause__ is None and "line 2" in str(caught.value)


def test_a_bad_provider_options_value_is_never_repeated() -> None:
    bad = definition(components={"search": {"defaults": {"provider_options": ["api_key", "sk-SECRET-789"]}}})

    with pytest.raises(PresetValidationError) as caught:
        PresetRegistry().add(bad)

    assert "sk-SECRET-789" not in str(caught.value)


def test_a_yaml_preset_holds_the_strings_a_json_one_would(tmp_path: Path, fake_provider: Any) -> None:
    path = tmp_path / "preset.yaml"
    path.write_text(
        "spec_version: 1\nid: acme.research\nversion: 1.0.0\napplies_to: [search]\n"
        "components:\n  search:\n    defaults:\n      provider_options: {startPublishedDate: 2025-01-01}\n"
    )
    router = WebRouter("fake")
    router.presets.load(path)

    router.search("q", preset=REF)

    assert searched(router).requests[0].provider_options == {"startPublishedDate": "2025-01-01"}


@pytest.mark.parametrize(
    "value",
    ["!!timestamp 2024-02-30", "!!int sk-SECRET-1", "!!binary aGVsbG8=", "!!set {a: null}"],
)
def test_a_yaml_value_json_cannot_hold_is_a_preset_error_that_quotes_nothing(tmp_path: Path, value: str) -> None:
    path = tmp_path / "preset.yaml"
    path.write_text(
        "spec_version: 1\nid: acme.research\nversion: 1.0.0\napplies_to: [search]\n"
        f"components:\n  search:\n    defaults:\n      provider_options: {{value: {value}}}\n"
    )

    with pytest.raises(PresetError) as caught:
        PresetRegistry().load(path)

    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_a_file_that_is_not_utf8_carries_no_cause(tmp_path: Path) -> None:
    path = tmp_path / "preset.json"
    path.write_bytes('{"title": "café", "provider_options": {"api_key": "sk-SECRET-2"}}'.encode("latin-1"))

    with pytest.raises(PresetError) as caught:
        PresetRegistry().load(path)

    assert caught.value.__cause__ is None
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize(
    "given",
    [
        json.dumps(definition(components={"search": {"defaults": {"provider_options": {"api_key": "sk-x"}}}})),
        "acme.research@1.0",
        "Summer.2026_pass",
    ],
)
def test_a_string_that_is_not_a_reference_is_never_repeated(given: str) -> None:
    with pytest.raises(PresetValidationError) as caught:
        WebRouter("auto", preset=given)

    assert given not in str(caught.value) and "sk-x" not in str(caught.value)
    assert "namespace.name@major.minor.patch" in str(caught.value)


def test_a_key_passed_as_a_reference_is_never_repeated() -> None:
    key = "tvly-FAKE0123"

    with pytest.raises(PresetValidationError) as caught:
        WebRouter("auto", preset=key)

    assert key not in str(caught.value)


def test_a_read_only_mapping_is_a_definition_like_any_other() -> None:
    from types import MappingProxyType

    nested = definition()
    nested["components"] = MappingProxyType(nested["components"])

    added = PresetRegistry().add(MappingProxyType(nested))

    assert added.section("search").defaults == {"max_results": 5}


@pytest.mark.parametrize(
    "defaults",
    [{"max_results": "sk-FAKE-KEY-123"}, {"provider": "sk-FAKE-KEY-123"}],
    ids=["bad value", "unknown provider"],
)
def test_a_definitions_bad_value_is_never_repeated(defaults: dict[str, Any]) -> None:
    with pytest.raises(PresetValidationError) as caught:
        PresetRegistry().add(definition(components={"search": {"defaults": defaults}}))

    assert "sk-FAKE-KEY-123".lower() not in str(caught.value).lower()


@pytest.mark.parametrize(
    "text",
    ['{"x": NaN}', '{"x": Infinity}'],
)
def test_nan_and_infinity_in_a_json_file_are_refused(text: str, tmp_path: Path) -> None:
    path = tmp_path / "preset.json"
    path.write_text(
        '{"spec_version": 1, "id": "acme.research", "version": "1.0.0", "applies_to": ["search"], '
        '"components": {"search": {"defaults": {"provider_options": ' + text + "}}}}"
    )

    with pytest.raises(PresetValidationError) as caught:
        PresetRegistry().load(path)

    assert [issue.field for issue in caught.value.issues] == ["components.search.defaults.provider_options.x"]


def test_nan_is_refused_in_yaml_and_in_a_definition_built_in_code(tmp_path: Path) -> None:
    path = tmp_path / "preset.yaml"
    path.write_text(
        "spec_version: 1\nid: acme.research\nversion: 1.0.0\napplies_to: [browser]\n"
        "components:\n  browser:\n    defaults: {timeout: .nan}\n"
    )
    with pytest.raises(PresetValidationError):
        PresetRegistry().load(path)
    with pytest.raises(PresetValidationError):
        PresetRegistry().add(definition(components={"browser": {"defaults": {"timeout": float("inf")}}}))


@pytest.mark.parametrize(
    ("component", "defaults", "field"),
    [
        ("search", {"provider_options": {"k": {1, 2}}}, "components.search.defaults.provider_options.k"),
        ("research", {"output_schema": {"type": object()}}, "components.research.defaults.output_schema.type"),
    ],
    ids=["set", "object"],
)
def test_a_definition_built_in_code_holds_only_what_json_can(
    component: str, defaults: dict[str, Any], field: str
) -> None:
    """A file is read as JSON or YAML; one built in code must hold no more, or validate() and meta fail later."""
    with pytest.raises(PresetValidationError) as caught:
        PresetRegistry().add(definition(components={component: {"defaults": defaults}}))

    assert [issue.field for issue in caught.value.issues] == [field]
