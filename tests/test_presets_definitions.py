"""Presets: a definition holds JSON and is compared as JSON, however it was built or read.

Nothing here calls a backend: these load, add and resolve definitions only.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.presets_helpers import REF, definition
from web_router import Preset, PresetRegistry, PresetValidationError
from web_router.types import Viewport


def test_a_model_value_is_taken_from_a_mapping_as_from_a_preset() -> None:
    sections = {"browser": {"defaults": {"viewport": Viewport(width=800, height=600)}}}

    from_model = PresetRegistry().add(Preset.model_validate(definition(components=sections)))
    from_mapping = PresetRegistry().add(definition(components=sections))

    assert from_mapping == from_model


def test_a_key_that_is_not_a_string_is_refused_by_its_own_message() -> None:
    with pytest.raises(PresetValidationError) as caught:
        PresetRegistry().add(definition(components={"search": {"defaults": {"provider_options": {"a": {1: "x"}}}}}))

    [issue] = caught.value.issues
    assert issue.field == "components.search.defaults.provider_options.a"
    assert "key that is not a string" in issue.message and "set or bytes" not in issue.message


@pytest.mark.parametrize("value", [(item for item in "ab"), threading.Lock()], ids=["generator", "lock"])
def test_a_value_that_cannot_be_copied_is_a_preset_error(value: Any) -> None:
    with pytest.raises(PresetValidationError):
        PresetRegistry().add(definition(components={"search": {"defaults": {"provider_options": {"a": value}}}}))


def test_yaml_reads_only_true_and_false_as_booleans(tmp_path: Path) -> None:
    """YAML 1.1 reads NO, yes and on as booleans; a country code or a vendor's value must arrive as written."""
    pytest.importorskip("yaml")
    path = tmp_path / "preset.yaml"
    path.write_text(
        "spec_version: 1\nid: acme.research\nversion: 1.0.0\napplies_to: [search]\ncomponents:\n  search:\n"
        "    defaults:\n      provider_options: {country: NO, a: yes, b: on, c: true, d: FALSE}\n"
    )

    [preset] = PresetRegistry().load(path)

    options = preset.components["search"].defaults["provider_options"]
    assert options == {"country": "NO", "a": "yes", "b": "on", "c": True, "d": False}


def test_a_lock_tells_true_from_one() -> None:
    schema = {"type": "boolean", "enum": [True, False]}
    registry = PresetRegistry()
    registry.add(definition(components={"research": {"defaults": {"output_schema": schema}}}))

    registry.resolve("research", REF, output_schema={"type": "boolean", "enum": [True, False]})
    with pytest.raises(PresetValidationError, match="output_schema"):
        registry.resolve("research", REF, output_schema={"type": "boolean", "enum": [1, 0]})


def _options(value: Any) -> dict[str, Any]:
    return definition(components={"search": {"defaults": {"provider_options": {"x": value}}}})


def test_a_version_reloaded_with_true_changed_to_one_is_refused(tmp_path: Path) -> None:
    first, changed = tmp_path / "first.json", tmp_path / "changed.json"
    first.write_text(json.dumps(_options(True)))
    changed.write_text(json.dumps(_options(1)))
    registry = PresetRegistry()
    registry.load(first)

    with pytest.raises(PresetValidationError, match="already loaded"):
        registry.load(changed)
    registry.load(first)  # the same definition again is still a no-op
    assert registry.get(REF).components["search"].defaults["provider_options"] == {"x": True}


def test_one_file_defining_a_version_twice_with_true_and_one_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "both.json"
    path.write_text(json.dumps([_options(True), _options(1)]))

    with pytest.raises(PresetValidationError, match="more than once"):
        PresetRegistry().load(path)
