"""SqlPresetStore runs only on a database its shipped migrations brought up to date."""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

import pytest

from web_router import (
    Preset,
    PresetStoreOutdatedError,
    PresetUnreadableError,
    ProviderNotInstalledError,
    SqlPresetStore,
)

sqlalchemy = pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")

from web_router.presets import PRESET_TOOLS, tool_params  # noqa: E402
from web_router.presets_sql import upgrade  # noqa: E402
from web_router.presets_sql.__main__ import main  # noqa: E402


@pytest.fixture
def url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'presets.db'}"


def _tables(url: str) -> set[str]:
    engine = sqlalchemy.create_engine(url)
    try:
        return set(sqlalchemy.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_creates_the_presets_table_a_params_table_per_tool_and_its_own_version_record(url: str) -> None:
    assert upgrade(url) == "0001"

    assert _tables(url) == {
        "web_router_presets",
        "web_router_presets_alembic_version",
        *(f"web_router_{tool}_params" for tool in PRESET_TOOLS),
    }


def _columns(url: str, table: str) -> dict[str, Any]:
    engine = sqlalchemy.create_engine(url)
    try:
        return {column["name"]: column for column in sqlalchemy.inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


@pytest.mark.parametrize("tool", PRESET_TOOLS)
def test_each_tools_params_table_has_a_column_for_each_param(url: str, tool: str) -> None:
    upgrade(url)

    columns = _columns(url, f"web_router_{tool}_params")

    assert set(columns) - {"id"} == set(tool_params(tool))
    assert all(column["nullable"] for name, column in columns.items() if name != "id")


def test_the_presets_table_points_at_one_params_row_per_tool(url: str) -> None:
    upgrade(url)

    columns = _columns(url, "web_router_presets")

    assert set(columns) == {
        "name",
        "tool",
        "description",
        "created_at",
        "updated_at",
        *(f"{tool}_params_id" for tool in PRESET_TOOLS),
    }
    assert columns["name"]["primary_key"] == 1


def test_a_param_left_unset_is_stored_as_null_and_reads_back_unset(url: str) -> None:
    upgrade(url)
    store = SqlPresetStore(url)
    store.create(Preset(name="p", tool="search", params={"max_results": 3}))
    engine = sqlalchemy.create_engine(url)
    with engine.connect() as connection:
        row = connection.execute(sqlalchemy.text("SELECT max_results, country FROM web_router_search_params")).one()
    engine.dispose()

    assert tuple(row) == (3, None)
    assert store.get("p") == Preset(name="p", tool="search", params={"max_results": 3})
    store.close()


def test_upgrade_is_safe_to_run_again(url: str) -> None:
    upgrade(url)

    assert upgrade(url) == "0001"


def test_a_database_never_upgraded_is_refused_with_the_command_to_run(url: str) -> None:
    with pytest.raises(PresetStoreOutdatedError, match=r"python -m web_router\.presets_sql upgrade <url>") as refused:
        SqlPresetStore(url)

    assert refused.value.current is None
    assert refused.value.head == "0001"
    assert _tables(url) == set()


def test_a_database_a_newer_version_wrote_is_refused_as_newer_not_behind(url: str) -> None:
    upgrade(url)
    engine = sqlalchemy.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sqlalchemy.text("UPDATE web_router_presets_alembic_version SET version_num = 'older'"))
    engine.dispose()

    with pytest.raises(PresetStoreOutdatedError, match="at revision older, which this version") as refused:
        SqlPresetStore(url)
    assert refused.value.newer is True
    assert "Upgrade web-router" in str(refused.value)


def test_a_row_this_version_cannot_read_is_refused_by_get_and_skipped_by_list(
    url: str, caplog: pytest.LogCaptureFixture
) -> None:
    upgrade(url)
    store = SqlPresetStore(url)
    store.create(Preset(name="good", tool="search"))
    store.create(Preset(name="old", tool="search", params={"include_domains": ["a.com"]}))
    engine = sqlalchemy.create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            sqlalchemy.text(
                "UPDATE web_router_search_params SET include_domains = :domains WHERE include_domains IS NOT NULL"
            ),
            {"domains": json.dumps(["a.com"] * 51)},
        )
    engine.dispose()

    with pytest.raises(PresetUnreadableError, match="preset 'old'"):
        store.get("old")
    assert [preset.name for preset in store.list()] == ["good"]
    assert "preset 'old'" in caplog.text
    store.close()


def test_the_command_line_upgrades_without_echoing_the_url(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["upgrade", url]) == 0

    out = capsys.readouterr().out
    assert out == "presets schema is at revision 0001\n"
    assert "web_router_presets" in _tables(url)


def test_without_the_sql_extra_the_store_says_which_extra_to_install(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def no_sql(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "web_router.presets_sql" and args[2] and "_sql" in args[2]:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sql)

    with pytest.raises(ProviderNotInstalledError, match="--extra sql"):
        SqlPresetStore(url)


def _broken(url: str, statement: str) -> SqlPresetStore:
    upgrade(url)
    store = SqlPresetStore(url)
    store.create(Preset(name="good", tool="search", params={"max_results": 3}))
    store.create(Preset(name="bad", tool="research", params={"output_schema": {"type": "object"}}))
    engine = sqlalchemy.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sqlalchemy.text(statement))
    engine.dispose()
    return store


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM web_router_research_params",
        "UPDATE web_router_research_params SET output_schema = '{not json'",
    ],
    ids=["missing params row", "malformed json"],
)
def test_a_preset_whose_params_cannot_be_read_is_unreadable_not_empty(url: str, statement: str) -> None:
    store = _broken(url, statement)

    assert [preset.name for preset in store.list()] == ["good"]
    with pytest.raises(PresetUnreadableError, match="preset 'bad'"):
        store.get("bad")
    store.close()
