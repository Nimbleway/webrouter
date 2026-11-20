"""The Web Catalog tab of the docs site: built from the shipped catalog into a copy, wired into docs.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.data_api_docs import DOCS, INDEX_FILE, TAB, build, index_entries, with_catalog_tab
from scripts.data_api_page import PAGES_DIR
from web_router.vendors.catalog import builtin_catalog


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("site") / "docs"
    build(DOCS, out, builtin_catalog())
    return out


def _catalog_group(config: dict[str, Any]) -> dict[str, Any]:
    tab = next(tab for tab in config["navigation"]["tabs"] if tab["tab"] == TAB)
    return tab["groups"][0]


def test_every_shipped_vendor_gets_a_page_and_nothing_else(site: Path) -> None:
    ids = {vendor.vendor for vendor in builtin_catalog().all()}
    assert {path.stem for path in (site / PAGES_DIR).glob("*.mdx")} == ids


def test_the_catalog_tab_lists_every_page_by_category_collapsed(site: Path) -> None:
    group = _catalog_group(json.loads((site / "docs.json").read_text(encoding="utf-8")))
    assert group["pages"][0] == PAGES_DIR  # the catalog page lands the tab
    sections = group["pages"][1:]
    assert all(section["expanded"] is False for section in sections)
    listed = [page for section in sections for page in section["pages"]]
    assert sorted(listed) == sorted(f"{PAGES_DIR}/{vendor.vendor}" for vendor in builtin_catalog().all())
    for page in listed:
        assert (site / f"{page}.mdx").is_file()


def test_the_docs_have_two_tabs_and_the_navbar_is_unchanged() -> None:
    config = json.loads((DOCS / "docs.json").read_text(encoding="utf-8"))
    assert [tab["tab"] for tab in config["navigation"]["tabs"]] == ["Documentation", TAB]
    assert all("label" not in link for link in config["navbar"]["links"])
    methods = next(g for g in config["navigation"]["tabs"][0]["groups"] if g["group"] == "Methods")["pages"]
    assert methods.index("vendors") == methods.index("live-data") + 1
    assert methods.index("browser") == methods.index("vendors") + 1


def test_the_committed_docs_are_left_untouched(site: Path) -> None:
    committed = _catalog_group(json.loads((DOCS / "docs.json").read_text(encoding="utf-8")))
    assert committed["pages"] == [PAGES_DIR]
    assert not (DOCS / INDEX_FILE).exists()
    assert not (DOCS / PAGES_DIR).exists()


def test_build_refuses_to_write_into_the_committed_docs() -> None:
    with pytest.raises(SystemExit):
        build(DOCS, DOCS, builtin_catalog())


def test_the_gallery_index_is_a_named_export_with_every_vendor(site: Path) -> None:
    text = (site / INDEX_FILE).read_text(encoding="utf-8")
    prefix = "export const DATA_APIS = "
    line = next(line for line in text.splitlines() if line.startswith(prefix))
    data = json.loads(line.removeprefix(prefix).rstrip(";"))
    assert len(data["vendors"]) == len(builtin_catalog())
    assert sum(category["count"] for category in data["categories"]) == len(builtin_catalog())


def test_index_terms_cover_sites_and_endpoint_names() -> None:
    rows = {row["id"]: row for row in index_entries(builtin_catalog())["vendors"]}
    assert "youtube.com" in rows["youtube"]["terms"]


def test_every_index_row_has_an_icon(site: Path) -> None:
    text = (site / INDEX_FILE).read_text(encoding="utf-8")
    data = json.loads(text.split("export const DATA_APIS = ", 1)[1].rstrip().rstrip(";"))
    for row in data["vendors"]:
        assert row["icon"].startswith("/logo/data-apis/"), row["id"]
        assert (site / row["icon"].lstrip("/")).is_file(), row["id"]


def test_index_rows_say_whether_a_key_is_needed() -> None:
    rows = {row["id"]: row for row in index_entries(builtin_catalog())["vendors"]}
    assert rows["hunter"]["needs_key"] is True
    assert rows["rcc-acis"]["needs_key"] is False


def test_with_catalog_tab_replaces_rather_than_duplicates() -> None:
    config = {"navigation": {"tabs": [{"tab": TAB, "groups": [{"group": TAB, "pages": [PAGES_DIR]}]}]}}
    once = with_catalog_tab(config, builtin_catalog())
    twice = with_catalog_tab(once, builtin_catalog())
    assert _catalog_group(twice)["pages"][0] == PAGES_DIR
    assert all(isinstance(section, dict) for section in _catalog_group(twice)["pages"][1:])
    assert len(_catalog_group(twice)["pages"]) == 1 + len({vendor.category for vendor in builtin_catalog().all()})


def test_with_catalog_tab_needs_the_tab() -> None:
    with pytest.raises(SystemExit):
        with_catalog_tab({"navigation": {"tabs": []}}, builtin_catalog())
