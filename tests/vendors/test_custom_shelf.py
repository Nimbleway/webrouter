"""A vendor file in the user's own catalog directory can sit on the custom shelf; an unknown shelf still fails."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.vendors.data import OAUTH
from web_router.constants_vendors import VENDOR_ENV_CATALOG_DIR
from web_router.exceptions import ConfigurationError
from web_router.vendors.catalog import get_catalog, reload_catalog


def _inhouse(tmp_path: Path, category: str) -> None:
    extra = {**copy.deepcopy(OAUTH), "vendor": "inhouse", "name": "In House", "category": category}
    for endpoint in extra["endpoints"]:
        endpoint["id"] = endpoint["id"].replace("oauthco.", "inhouse.")
    for spec in extra["auth"]["credentials"].values():
        spec["env"] = [name.replace("OAUTHCO", "INHOUSE") for name in spec["env"]]
    (tmp_path / "inhouse.json").write_text(json.dumps(extra), encoding="utf-8")


@pytest.fixture
def directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv(VENDOR_ENV_CATALOG_DIR, str(tmp_path))
    yield tmp_path
    monkeypatch.delenv(VENDOR_ENV_CATALOG_DIR)
    reload_catalog()


def test_a_custom_vendor_loads_beside_the_shipped_ones(directory: Path) -> None:
    _inhouse(directory, "custom")
    catalog = reload_catalog()
    assert catalog.vendor("inhouse").category == "custom"
    assert get_catalog().vendor("openalex").vendor == "openalex"


def test_an_unknown_shelf_is_still_refused(directory: Path) -> None:
    _inhouse(directory, "other")
    with pytest.raises(ConfigurationError, match=r"inhouse\.json"):
        reload_catalog()
