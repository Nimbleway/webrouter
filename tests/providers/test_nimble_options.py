"""Nimble search: how each shared option reaches the API, and what comes back for it."""

from __future__ import annotations

import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.types import Recency, SearchRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from web_router.providers.nimble import NimbleProvider


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleProvider()


@pytest.mark.parametrize("recency", ["day", "week", "month", "year"])
def test_recency_is_sent_as_time_range(provider: NimbleProvider, recency: Recency) -> None:
    params = provider._build_payload(SearchRequest(query="hi", recency=recency))
    assert params == {"query": "hi", "time_range": recency}


def test_effort_is_refused_unsent_since_nimble_has_no_search_tier(provider: NimbleProvider) -> None:
    assert "effort" not in NimbleProvider.supported_parameters
    with pytest.raises(UnsupportedParameterError, match="effort"):
        provider.search(SearchRequest(query="hi", effort="low"))
