from __future__ import annotations

import pytest
from pydantic import ValidationError

from web_router.types import SearchRequest, SearchResponse, SearchResult


def test_request_requires_a_non_empty_query() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(query="")


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
