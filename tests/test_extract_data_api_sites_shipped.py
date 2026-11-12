"""The shipped catalog's ``sites``: well-known sites list their own data API, a site no entry serves lists none."""

from __future__ import annotations

import pytest

from web_router.data_api_sites import site_matches
from web_router.vendors.catalog import builtin_catalog
from web_router.vendors.credentials import EnvCredentials


@pytest.mark.parametrize(
    ("url", "data_api"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://youtu.be/dQw4w9WgXcQ", "youtube"),
        ("https://github.com/Nimbleway/webrouter/issues", "github"),
        ("https://stackoverflow.com/questions/231767", "stackexchange"),
        ("https://en.wikipedia.org/wiki/Web_scraping", "wikipedia"),
        ("https://www.npmjs.com/package/react", "npm"),
        ("https://twitter.com/nasa", "x"),
    ],
)
def test_a_well_known_site_lists_its_own_data_api(url: str, data_api: str) -> None:
    matches = site_matches(builtin_catalog(), url, EnvCredentials())
    assert matches
    assert matches[0].data_api == data_api


def test_the_path_ranks_the_endpoint_it_names_first() -> None:
    matches = site_matches(builtin_catalog(), "https://github.com/Nimbleway/webrouter/issues", EnvCredentials())
    assert matches[0].endpoint.startswith("github.issues.")


def test_a_site_no_entry_serves_lists_none() -> None:
    assert site_matches(builtin_catalog(), "https://www.nytimes.com/", EnvCredentials()) == []
