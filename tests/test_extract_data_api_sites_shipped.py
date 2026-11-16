"""The shipped catalog's ``sites``: well-known pages list the data APIs that serve them, read locally."""

from __future__ import annotations

import pytest

from web_router.data_api_discovery import site_matches
from web_router.vendors.catalog import builtin_catalog
from web_router.vendors.credentials import EnvCredentials


@pytest.mark.parametrize(
    ("url", "data_api"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://youtu.be/dQw4w9WgXcQ", "youtube"),
        ("https://github.com/a/b", "github"),
        ("https://en.wikipedia.org/wiki/X", "wikipedia"),
        ("https://pypi.org/project/x", "pypi"),
        ("https://stackoverflow.com/questions/1/x", "stackexchange"),
        ("https://x.com/someone", "x"),
    ],
)
def test_a_well_known_page_lists_its_sites_data_api(url: str, data_api: str) -> None:
    matches = site_matches(url, EnvCredentials(), catalog=builtin_catalog())
    assert matches
    assert matches[0].data_api == data_api


def test_a_page_no_shipped_entry_serves_lists_nothing() -> None:
    assert site_matches("https://example.com", EnvCredentials(), catalog=builtin_catalog()) == []
