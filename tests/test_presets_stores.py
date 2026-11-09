"""Every built-in preset store keeps the same contract: get, list, and a create that never overwrites."""

from __future__ import annotations

import importlib.util
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from web_router import (
    FilePresetStore,
    InMemoryPresetStore,
    Preset,
    PresetNameTakenError,
    PresetStore,
)

SQL_INSTALLED = importlib.util.find_spec("sqlalchemy") is not None and importlib.util.find_spec("alembic") is not None


def _sql_store(tmp_path: Path) -> PresetStore:
    from web_router.presets_sql import SqlPresetStore, upgrade

    url = f"sqlite:///{tmp_path / 'presets.db'}"
    upgrade(url)
    return SqlPresetStore(url)


STORES: dict[str, Callable[[Path], PresetStore]] = {
    "memory": lambda _: InMemoryPresetStore(),
    "file": lambda tmp_path: FilePresetStore(tmp_path / "presets"),
    "sql": _sql_store,
}


@pytest.fixture(params=list(STORES))
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[PresetStore]:
    if request.param == "sql" and not SQL_INSTALLED:
        pytest.skip("the sql extra is not installed")
    built = STORES[request.param](tmp_path)
    yield built
    close = getattr(built, "close", None)
    if close is not None:
        close()


NEWS = Preset(
    name="fresh_news",
    tool="search",
    params={"search_depth": "fast", "max_results": 10, "include_domains": ["reuters.com"]},
    description="Recent news from wires",
)
BROWSER = Preset(
    name="stealthy", tool="open_browser", params={"stealth": "high", "viewport": {"width": 1, "height": 2}}
)


def test_each_store_is_a_preset_store(store: PresetStore) -> None:
    assert isinstance(store, PresetStore)


def test_an_empty_store_has_nothing_to_get_or_list(store: PresetStore) -> None:
    assert store.get("fresh_news") is None
    assert store.list() == []


def test_a_created_preset_reads_back_as_it_was_made(store: PresetStore) -> None:
    store.create(NEWS)
    store.create(BROWSER)

    assert store.get("fresh_news") == NEWS
    assert store.get("stealthy") == BROWSER
    assert sorted(store.list(), key=lambda preset: preset.name) == [NEWS, BROWSER]


def test_a_name_already_taken_is_refused_and_the_stored_preset_kept(store: PresetStore) -> None:
    store.create(NEWS)

    with pytest.raises(PresetNameTakenError, match="You already have a preset named 'fresh_news'"):
        store.create(Preset(name="fresh_news", tool="extract"))
    assert store.get("fresh_news") == NEWS


def test_names_are_case_sensitive(store: PresetStore, tmp_path: Path) -> None:
    (tmp_path / "Case").touch()
    if isinstance(store, FilePresetStore) and (tmp_path / "case").exists():
        pytest.skip("this filesystem cannot hold two files whose names differ only in case")
    store.create(Preset(name="Report", tool="search"))
    store.create(Preset(name="report", tool="answer"))

    assert {preset.name for preset in store.list()} == {"Report", "report"}


def test_concurrent_creates_of_one_name_keep_exactly_one(store: PresetStore) -> None:
    taken: list[PresetNameTakenError] = []

    def create(tool: str) -> None:
        try:
            store.create(Preset(name="race", tool=tool))  # type: ignore[arg-type]
        except PresetNameTakenError as exc:
            taken.append(exc)

    threads = [threading.Thread(target=create, args=(tool,)) for tool in ("search", "extract") * 4]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(taken) == len(threads) - 1
    assert [preset.name for preset in store.list()] == ["race"]


def test_changing_what_a_store_returned_does_not_change_the_store(store: PresetStore) -> None:
    store.create(NEWS)
    got = store.get("fresh_news")
    assert got is not None

    got.params["max_results"] = 99

    assert store.get("fresh_news") == NEWS
