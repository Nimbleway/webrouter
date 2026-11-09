"""Linkup search: how each shared option reaches the API, and what comes back for it.

``fromDate`` as ``recency`` sets it was checked live on an answer; the depth
``effort`` picks was measured live on 2026-11-09, all four levels answering.
"""

from __future__ import annotations

from datetime import date

import pytest

from web_router.providers._clock import pinned_today
from web_router.providers.linkup import LinkupProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> LinkupProvider:
    monkeypatch.setenv("LINKUP_API_KEY", "test-key")
    return LinkupProvider()


@pytest.mark.parametrize(
    ("effort", "depth"), [("low", "flash"), ("medium", "fast"), ("high", "standard"), ("max", "deep")]
)
def test_effort_picks_linkups_depth_one_for_one(provider: LinkupProvider, effort: str, depth: str) -> None:
    assert provider._build_payload(SearchRequest(query="hi", effort=effort))["depth"] == depth  # type: ignore[arg-type]


@pytest.mark.parametrize(("recency", "start"), [("day", "2026-11-08"), ("month", "2026-10-10"), ("year", "2025-11-09")])
def test_recency_sets_from_date_counted_back_from_today(provider: LinkupProvider, recency: str, start: str) -> None:
    with pinned_today(date(2026, 11, 9)):
        payload = provider._build_payload(SearchRequest(query="hi", recency=recency))  # type: ignore[arg-type]
    assert payload["fromDate"] == start and "toDate" not in payload
