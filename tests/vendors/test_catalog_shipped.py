"""Every shipped vendor file held to the catalog's rules, and the files generated from them kept current."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from tests.vendors.data import OAUTH, shipped_dir
from web_router.constants_vendors import VENDOR_CATEGORIES, VENDOR_CUSTOM_CATEGORY, VENDOR_ENV_CATALOG_DIR
from web_router.vendors.catalog import builtin_catalog, get_catalog, load_vendor_file, reload_catalog

SHIPPED = builtin_catalog()


SHIPPED_FILES = sorted(shipped_dir().glob("*.json"))


def test_the_catalog_ships_vendors() -> None:
    assert {"datadog", "google-ads"} <= set(SHIPPED.names())
    assert len(SHIPPED_FILES) == len(SHIPPED)


@pytest.mark.parametrize("path", SHIPPED_FILES, ids=lambda path: path.stem)
def test_a_shipped_file(path: Path) -> None:
    vendor = load_vendor_file(path)
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", vendor.curated)
    assert vendor.endpoints, "a vendor file names at least one endpoint"
    for endpoint in vendor.endpoints:
        assert endpoint.name and endpoint.docs_url.startswith("https://")
        if endpoint.verified.live:
            assert re.match(r"^\d{4}-\d{2}-\d{2}$", endpoint.verified.live)
        if endpoint.verified.docs:
            assert endpoint.verified.docs.startswith("https://")
        for name, param in endpoint.params.items():
            assert param.location in {"path", "query", "body", "header"}, name
    # A vendor with credentials names how to get each one.
    for name, credential in vendor.auth.credentials.items():
        assert credential.env, name


def test_every_shipped_write_says_so() -> None:
    """A mutate, delete or create endpoint not marked write would skip the allow_writes gate."""
    pattern = re.compile(r"(mutate|delete|remove|create|update|send|post|publish|upload)", re.IGNORECASE)
    for vendor in SHIPPED.all():
        for endpoint in vendor.endpoints:
            if endpoint.method == "DELETE" or (endpoint.method in {"PUT", "PATCH"}):
                assert endpoint.kind == "write", endpoint.id
            if pattern.search(endpoint.path.rsplit("/", 1)[-1]) and endpoint.method != "GET":
                assert endpoint.kind == "write" or endpoint.notes, f"{endpoint.id}: explain why this POST reads"


def test_every_shelf_is_used_and_none_is_a_catch_all() -> None:
    """A vendor that fits no shelf needs a new one, not an "other" bin, and no shelf sits empty.

    The custom shelf is the exception: it is for a user's own files and ships empty.
    """
    assert "other" not in VENDOR_CATEGORIES
    shipped = {vendor.category for vendor in SHIPPED.all()}
    assert shipped == set(VENDOR_CATEGORIES) - {VENDOR_CUSTOM_CATEGORY}


def test_shipped_env_variables_belong_to_one_vendor() -> None:
    """Two vendors reading one variable would send one vendor's key to the other."""
    owner: dict[str, str] = {}
    for vendor in SHIPPED.all():
        for credential in vendor.auth.credentials.values():
            for variable in credential.env:
                assert owner.setdefault(variable, vendor.vendor) == vendor.vendor, variable


def test_vendors_md_and_env_example_are_current() -> None:
    from scripts.vendors import ENV_EXAMPLE, VENDORS_MD, regenerate_env, regenerate_markdown

    markdown = VENDORS_MD.read_text(encoding="utf-8")
    env = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert regenerate_markdown(markdown, SHIPPED) == markdown, "VENDORS.md is stale: run `make vendors`."
    assert regenerate_env(env, SHIPPED) == env, ".env.example is stale: run `make vendors`."


def test_the_generated_files_ignore_a_local_vendor_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from scripts.vendors import main

    extra = {**copy.deepcopy(OAUTH), "vendor": "localco", "name": "Local Co"}
    for endpoint in extra["endpoints"]:
        endpoint["id"] = endpoint["id"].replace("oauthco.", "localco.")
    for spec in extra["auth"]["credentials"].values():
        spec["env"] = [name.replace("OAUTHCO", "LOCALCO") for name in spec["env"]]
    (tmp_path / "localco.json").write_text(json.dumps(extra), encoding="utf-8")
    monkeypatch.setenv(VENDOR_ENV_CATALOG_DIR, str(tmp_path))
    reload_catalog()
    try:
        assert "localco" in get_catalog().names()
        assert "localco" not in builtin_catalog().names()
        assert main(["--check"]) == 0
    finally:
        monkeypatch.delenv(VENDOR_ENV_CATALOG_DIR)
        reload_catalog()
