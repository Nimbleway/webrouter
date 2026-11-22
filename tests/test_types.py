from __future__ import annotations

import pytest
from pydantic import ValidationError

from web_router.snippet import SNIPPET_LIMIT, as_snippet
from web_router.types import SearchRequest, SearchResponse, SearchResult


def test_request_requires_a_non_empty_query() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(query="")


def test_a_query_that_is_not_blank_is_kept_as_written() -> None:
    assert SearchRequest(query="  hi \n").query == "  hi \n"


def test_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(query="hi", nonsense=True)


def test_request_defaults_leave_provider_choices_alone() -> None:
    request = SearchRequest(query="hi")
    assert request.max_results is None
    assert request.search_depth is None
    assert request.provider_options == {}


def test_response_exposes_length_and_urls() -> None:
    response = SearchResponse(
        provider="fake",
        query="hi",
        results=[SearchResult(url="https://a.com"), SearchResult(url="https://b.com")],
    )
    assert len(response) == 2
    assert response.urls == ["https://a.com", "https://b.com"]


def test_a_page_long_description_is_cut_to_a_snippet_and_content_keeps_the_page() -> None:
    page = "word " * 3000
    result = SearchResult(url="https://a.com", description=page, content=page)
    assert len(result.description) <= SNIPPET_LIMIT and result.description.endswith("…")
    assert page.startswith(result.description[:-1]) and result.content == page


def test_a_snippet_survives_being_cut_again() -> None:
    once = as_snippet("x" * 2000)
    assert len(once) == SNIPPET_LIMIT and as_snippet(once) == once
    assert SearchResult(url="https://a.com", description=once).description == once


def test_a_short_description_is_kept_as_written() -> None:
    assert SearchResult(url="https://a.com", description="A short summary.").description == "A short summary."


@pytest.mark.parametrize("separator", [" ", "\n", "\t", "\r", "\u00a0", "\u2003"])
def test_a_description_broken_by_whitespace_is_cut_between_words(separator: str) -> None:
    lines = separator.join(["alpha", "beta"] * 200)
    assert SearchResult(url="https://a.com", description=lines).description.split()[-1] in {"alpha\u2026", "beta\u2026"}
