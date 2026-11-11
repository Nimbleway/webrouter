"""The catalog format's rules, the loader and lookups. Every shipped file is held to them in
``test_catalog_shipped.py``."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.vendors.data import ACME, OAUTH, shipped_dir
from web_router.constants_vendors import VENDOR_ENV_CATALOG_DIR
from web_router.exceptions import ConfigurationError
from web_router.exceptions_vendors import VendorNotFoundError
from web_router.vendors.catalog import (
    VendorCatalog,
    load_vendor_file,
    reload_catalog,
)
from web_router.vendors.schema import Vendor


def _broken(change: Any) -> dict[str, Any]:
    vendor = copy.deepcopy(ACME)
    change(vendor)
    return vendor


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda v: v.update(surprise=1), "Extra inputs are not permitted"),
        (lambda v: v.update(base_url="http://api.acme.test"), "must be https"),
        (lambda v: v["settings"]["region"].pop("choices"), "must declare choices"),
        (lambda v: v.update(base_url="https://api.{zone}.acme.test"), "not a setting"),
        (lambda v: v["auth"]["inject"].append({"in": "header", "name": "X", "value": "{nope}"}), "neither"),
        (lambda v: v["auth"]["inject"].append({"in": "header", "name": "X", "value": "{access_token}"}), "oauth2"),
        (lambda v: v["endpoints"][0].update(id="other.metrics.query"), "must start with 'acme.'"),
        (lambda v: v["endpoints"].append(copy.deepcopy(v["endpoints"][0])), "more than once"),
        (lambda v: v["endpoints"][2].update(path="/v1/monitor/{id}"), "has no 'path' parameter"),
        (lambda v: v["endpoints"][0]["params"].update(x={"in": "path"}), "is not in the path"),
        (lambda v: v["endpoints"][0]["params"].update(x={"in": "body"}), "cannot carry body"),
        (lambda v: v["endpoints"][0].update(kind="maybe"), "kind"),
        (lambda v: v["endpoints"][0].update(verified={}), "verified needs"),
        (lambda v: v.update(category="misc"), "is not one of"),
        (lambda v: v["auth"]["credentials"]["api_key"].update(env=["lower"]), "environment variable"),
    ],
)
def test_the_format_refuses(change: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Vendor.model_validate(_broken(change))


def test_an_oauth_block_must_be_complete() -> None:
    vendor = copy.deepcopy(OAUTH)
    del vendor["auth"]["oauth2"]["refresh_token"]
    with pytest.raises(ValidationError, match="refresh_token grant needs"):
        Vendor.model_validate(vendor)


def test_a_file_must_be_named_for_its_vendor(tmp_path: Path) -> None:
    path = tmp_path / "not-acme.json"
    path.write_text(json.dumps(ACME))
    with pytest.raises(ConfigurationError, match=r"not-acme\.json.*declares vendor 'acme'"):
        load_vendor_file(path)


def test_an_invalid_file_names_itself(tmp_path: Path) -> None:
    path = tmp_path / "acme.json"
    path.write_text("{not json")
    with pytest.raises(ConfigurationError, match=r"acme\.json is invalid"):
        load_vendor_file(path)


def test_an_extra_directory_adds_and_replaces_vendors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "acme.json").write_text(json.dumps(ACME))
    replaced = {**json.loads((shipped_dir() / "datadog.json").read_text()), "summary": "Pinned locally."}
    (tmp_path / "datadog.json").write_text(json.dumps(replaced))
    monkeypatch.setenv(VENDOR_ENV_CATALOG_DIR, str(tmp_path))
    try:
        catalog = reload_catalog()
        assert "acme" in catalog.names()
        assert catalog.vendor("datadog").summary == "Pinned locally."
        assert "google-ads" in catalog.names()
    finally:
        monkeypatch.delenv(VENDOR_ENV_CATALOG_DIR)
        reload_catalog()


def test_unknown_names_list_what_exists(catalog: VendorCatalog) -> None:
    with pytest.raises(VendorNotFoundError, match="Known vendors: acme, basicco"):
        catalog.vendor("nope")
    with pytest.raises(VendorNotFoundError, match=r"'acme' has: acme\.metrics\.query"):
        catalog.endpoint("acme.nope")
    with pytest.raises(VendorNotFoundError, match="No vendor is named 'zzz'"):
        catalog.endpoint("zzz.thing")


def test_find_ranks_by_words_and_filters(catalog: VendorCatalog) -> None:
    found = catalog.find("acme logs search")
    assert found[0].id == "acme.logs.search"
    assert [e.id for e in catalog.find("monitor", kind="write")] == ["acme.monitors.delete"]
    assert {e.vendor for e in catalog.find("", category="seo")} == {"querykey"}
    assert catalog.find("zebra unicorn") == []
    assert len(catalog.find("", vendor="acme")) == 4
    assert len(catalog.find("acme", limit=2)) == 2


def test_find_ignores_filler_words() -> None:
    """Words like "for" and "a" sit in every summary; on their own they must not make a match."""
    vendor = copy.deepcopy(ACME)
    vendor["endpoints"][0]["summary"] = "The value of a metric for a time window."
    catalog = VendorCatalog([Vendor.model_validate(vendor)])
    assert catalog.find("zebra for a") == []
    assert catalog.find("the metric for a window") == catalog.find("metric window")


def test_a_search_of_only_filler_words_finds_nothing(catalog: VendorCatalog) -> None:
    """An empty query lists every endpoint; one made only of filler words asks for nothing."""
    assert catalog.find("the") == []
    assert catalog.find("what is it") == []
    assert catalog.find("") != []
    assert catalog.find("the logs") == catalog.find("logs")


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.update(name="Acme\nEVIL=1"),
        lambda v: v.update(docs_url="https://docs.example.com\r\nX"),
        lambda v: v["settings"]["region"].update(default="us\nEVIL=1"),
    ],
)
def test_text_printed_into_the_generated_files_is_one_line(change: Any) -> None:
    vendor = copy.deepcopy(ACME)
    change(vendor)
    with pytest.raises(ValidationError, match="must be one line"):
        Vendor.model_validate(vendor)
