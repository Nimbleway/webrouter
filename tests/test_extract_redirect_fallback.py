"""An off-site page stays available when no later backend reads the requested URL."""

from __future__ import annotations

import asyncio

import pytest

from tests.test_auto_extract_retry import TEXT, _chain, registered
from tests.test_extract_junk_pages import _serving
from web_router.constants_extract import MSG_EXTRACT_OFF_SITE
from web_router.exceptions_typed import ExtractFailedError
from web_router.types import ExtractRequest

__all__ = ["registered"]

DOI = "https://doi.org/10.1038/nature14539"
ARTICLE = "https://www.nature.com/articles/nature14539"


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("later", ["none", "on-site", "unavailable", "gone"])
def test_an_off_site_page_is_kept_unless_a_later_backend_answers_or_reports_it_gone(
    registered: list[str], asynchronous: bool, later: str
) -> None:
    members = [_serving("first", TEXT, landed=ARTICLE)]
    if later != "none":
        members.append(
            _serving("second", TEXT, raises=later == "unavailable", status_code=404 if later == "gone" else None)
        )
    auto = _chain(registered, *members)
    request = ExtractRequest(urls=[DOI])
    try:
        if later == "gone":
            with pytest.raises(ExtractFailedError, match="404"):
                asyncio.run(auto.aextract(request)) if asynchronous else auto.extract(request)
            return
        response = asyncio.run(auto.aextract(request)) if asynchronous else auto.extract(request)
    finally:
        auto.close()

    document = response.documents[0]
    assert document.url == (DOI if later == "on-site" else ARTICLE)
    assert document.warnings == ([] if later == "on-site" else [MSG_EXTRACT_OFF_SITE])
    assert document.content == TEXT
    assert response.failures == []
