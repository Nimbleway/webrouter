"""FilePresetStore reads what people write by hand, and says which file is wrong when one is."""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

import pytest

from web_router import FilePresetStore, Preset, PresetUnreadableError, ProviderNotInstalledError


def test_create_writes_one_json_file_named_for_the_preset(tmp_path: Path) -> None:
    FilePresetStore(tmp_path).create(Preset(name="news", tool="search", params={"max_results": 3}))

    assert json.loads((tmp_path / "news.json").read_text()) == {
        "name": "news",
        "tool": "search",
        "params": {"max_results": 3},
    }


def test_a_file_may_hold_a_list_of_presets(tmp_path: Path) -> None:
    (tmp_path / "team.json").write_text(
        json.dumps([{"name": "a", "tool": "search"}, {"name": "b", "tool": "extract", "params": {"format": "text"}}])
    )

    assert {preset.name for preset in FilePresetStore(tmp_path).list()} == {"a", "b"}


def test_yaml_files_are_read_beside_json(tmp_path: Path) -> None:
    (tmp_path / "one.yaml").write_text("name: one\ntool: answer\nparams:\n  recency: week\n")
    (tmp_path / "two.yml").write_text("- name: two\n  tool: search\n")

    store = FilePresetStore(tmp_path)

    assert store.get("one") == Preset(name="one", tool="answer", params={"recency": "week"})
    assert store.get("two") == Preset(name="two", tool="search")


def test_other_files_in_the_directory_are_not_read(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# not a preset")

    assert FilePresetStore(tmp_path).list() == []


def test_a_missing_directory_reads_empty_and_is_made_by_the_first_create(tmp_path: Path) -> None:
    store = FilePresetStore(tmp_path / "nested" / "presets")
    assert store.list() == []

    store.create(Preset(name="p", tool="search"))

    assert (tmp_path / "nested" / "presets" / "p.json").is_file()


@pytest.mark.parametrize(
    ("content", "detail"),
    [
        ("{not json", "Expecting property name"),
        (json.dumps({"name": "p", "tool": "search", "params": {"max_results": "lots"}}), "max_results"),
        (json.dumps({"name": "p", "tool": "search", "params": {"depth": "deep"}}), "depth: unknown_param"),
    ],
)
def test_a_preset_this_version_cannot_read_is_skipped_by_list_and_raised_by_get(
    tmp_path: Path, content: str, detail: str, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "p.json").write_text(content)
    (tmp_path / "good.json").write_text(json.dumps({"name": "good", "tool": "search"}))
    store = FilePresetStore(tmp_path)

    assert [preset.name for preset in store.list()] == ["good"]
    assert "p.json" in caplog.text
    with pytest.raises(PresetUnreadableError, match=r"p\.json") as refused:
        store.get("p")
    assert detail in str(refused.value)


def test_one_name_in_two_files_is_refused_naming_both(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps({"name": "dup", "tool": "search"}))
    (tmp_path / "b.json").write_text(json.dumps({"name": "dup", "tool": "extract"}))

    store = FilePresetStore(tmp_path)

    with pytest.raises(PresetUnreadableError, match=r"a\.json") as refused:
        store.get("dup")
    assert "b.json" in str(refused.value)
    assert store.list() == []


def test_a_yaml_file_without_pyyaml_says_which_extra_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "one.yaml").write_text("name: one\ntool: search\n")
    real_import = builtins.__import__

    def no_yaml(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_yaml)

    with pytest.raises(ProviderNotInstalledError, match="--extra yaml"):
        FilePresetStore(tmp_path).list()


def test_a_file_that_is_not_utf8_is_one_bad_entry_not_a_broken_store(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_bytes(b"\xff\xfe")
    (tmp_path / "good.json").write_text(json.dumps({"name": "good", "tool": "search"}))
    store = FilePresetStore(tmp_path)

    assert [preset.name for preset in store.list()] == ["good"]
    with pytest.raises(PresetUnreadableError, match=r"bad\.json"):
        store.get("bad")
    store.create(Preset(name="fresh", tool="extract"))
    assert store.get("fresh") == Preset(name="fresh", tool="extract")


def test_an_unreadable_presets_error_names_the_field_never_its_value(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "sk-live-SEKRIT"
    (tmp_path / "p.json").write_text(json.dumps({"name": "p", "tool": "search", "params": {"api_key": secret}}))
    store = FilePresetStore(tmp_path)

    assert store.list() == []
    with pytest.raises(PresetUnreadableError) as refused:
        store.get("p")
    assert "api_key: unknown_param" in str(refused.value)
    assert secret not in str(refused.value)
    assert secret not in caplog.text
