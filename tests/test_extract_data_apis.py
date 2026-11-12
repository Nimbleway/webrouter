"""Data API matches on extracted pages: the data APIs that serve each page's site, found locally and never called."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.vendors.conftest import _COMMON, _endpoint
from web_router import AsyncWebRouter, WebRouter
from web_router.constants_vendors import DATA_API_EXTRACT_LIMIT, VENDOR_ENV_CATALOG_DIR
from web_router.data_api_discovery import site_matches
from web_router.exceptions import ConfigurationError
from web_router.vendors.catalog import load_vendor_file, reload_catalog
from web_router.vendors.credentials import EnvCredentials

CLIPS: dict[str, Any] = {
    **_COMMON,
    "vendor": "clips",
    "name": "Clips Data API",
    "category": "social",
    "summary": "Public videos and channels on clips.test.",
    "base_url": "https://api.clips.test",
    "sites": ["clips.test", "clp.test"],
    "auth": {
        "credentials": {"api_key": {"env": ["CLIPS_API_KEY"]}},
        "inject": [{"in": "query", "name": "key", "value": "{api_key}"}],
    },
    "endpoints": [
        _endpoint("clips.channels.list", "GET", "/channels", job="channels.list"),
        _endpoint("clips.videos.list", "GET", "/videos", job="videos.list"),
        _endpoint("clips.comments.insert", "POST", "/comments", kind="write", job="comments.insert"),
        _endpoint("clips.playlists.list", "GET", "/playlists", job="playlists.list"),
        _endpoint("clips.captions.list", "GET", "/captions", job="captions.list"),
    ],
}
KEYED = {"clips": {"api_key": "k"}}


@pytest.fixture(autouse=True)
def _clips_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    (tmp_path / "clips.json").write_text(json.dumps(CLIPS))
    monkeypatch.setenv(VENDOR_ENV_CATALOG_DIR, str(tmp_path))
    monkeypatch.delenv("CLIPS_API_KEY", raising=False)
    reload_catalog()
    yield
    monkeypatch.delenv(VENDOR_ENV_CATALOG_DIR)
    reload_catalog()


def test_a_page_on_a_served_site_lists_its_read_endpoints_those_its_path_names_first(
    fake_extract_provider: Any,
) -> None:
    with WebRouter("fake", vendor_credentials=KEYED) as router:
        response = router.extract("https://www.clips.test/videos/abc")
    matches = response.documents[0].data_apis
    assert len(matches) == DATA_API_EXTRACT_LIMIT
    assert matches[0].endpoint == "clips.videos.list"
    assert all(match.kind == "read" and match.data_api == "clips" for match in matches)
    assert all(match.configured and match.missing == [] for match in matches)


def test_an_unconfigured_match_says_what_would_add_it(fake_extract_provider: Any) -> None:
    with WebRouter("fake") as router:
        match = router.extract("https://clp.test/x").documents[0].data_apis[0]
    assert not match.configured
    assert match.missing == ["CLIPS_API_KEY"]


def test_configured_only_and_discovery_off_and_other_sites_list_nothing(fake_extract_provider: Any) -> None:
    with WebRouter("fake") as router:
        assert router.extract("https://clips.test/v", configured_only=True).documents[0].data_apis == []
        assert router.extract("https://clips.test/v", data_apis=False).documents[0].data_apis == []
        # A lookalike host is not a subdomain: "notclips.test" ends with "clips.test" but not ".clips.test".
        response = router.extract(["https://notclips.test/v", "https://example.com"])
    assert [document.data_apis for document in response.documents] == [[], []]


def test_each_page_gets_its_own_matches(fake_extract_provider: Any) -> None:
    with WebRouter("fake") as router:
        response = router.extract(["https://example.com", "https://m.clips.test/channels/1"])
    assert response.documents[0].data_apis == []
    assert response.documents[1].data_apis[0].endpoint == "clips.channels.list"


async def test_the_async_router_lists_them_too(fake_extract_provider: Any) -> None:
    async with AsyncWebRouter("fake", vendor_credentials=KEYED) as router:
        response = await router.extract("https://clips.test/playlists")
    assert response.documents[0].data_apis[0].endpoint == "clips.playlists.list"


def test_a_url_with_no_host_matches_nothing() -> None:
    assert site_matches("not a url", EnvCredentials()) == []


@pytest.mark.parametrize("site", ["https://clips.test", "Clips.test", "clips.test/videos", "localhost"])
def test_a_site_must_be_a_bare_lowercase_host(tmp_path: Path, site: str) -> None:
    vendor = copy.deepcopy(CLIPS)
    vendor["sites"] = [site]
    path = tmp_path / "clips.json"
    path.write_text(json.dumps(vendor))
    with pytest.raises(ConfigurationError, match="bare lowercase host"):
        load_vendor_file(path)
