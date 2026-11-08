"""Rule 7 with extract the only method offered: it never falls back to a search with no backend to run."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from web_router import Budget, Candidate, RouteDecision, RouteRequest, prices
from web_router.constants_merge import MSG_EXTRACT_NO_URL, NOTE_EXTRACT, NOTE_EXTRACT_URLS
from web_router.routing.merge import merge
from web_router.routing.url_policy import any_web_url, public_only

ONLY_EXTRACT = (Candidate(tool="extract", provider="jina"),)


def _merge(decision: RouteDecision, url_check: object = any_web_url) -> object:
    req = RouteRequest(prompt="q", candidates=ONLY_EXTRACT, deadline_s=1.0, budget=Budget(max_cost_usd=0.05))
    return merge(req, decision, {"extract": ("jina",)}, url_check=url_check)  # type: ignore[arg-type]


def _urls(*urls: str) -> RouteDecision:
    return RouteDecision(tools=["extract"], params={"extract": {"urls": list(urls)}})


def test_router_urls_dropped_for_cost_keep_extract_when_no_search_backend_is_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = SimpleNamespace(estimate_usd=0.01, estimate_is_floor=False, mode=None, results_assumed=None)
    monkeypatch.setattr(prices, "price", lambda *_: row)
    merged = _merge(_urls(*(f"https://site.test/{i}" for i in range(50))))
    assert (merged.tool, merged.providers, merged.params) == ("extract", ("jina",), {})  # type: ignore[attr-defined]
    assert NOTE_EXTRACT not in merged.notes.ignored  # type: ignore[attr-defined]
    assert "cost cap" in merged.notes.ignored["params.extract.urls"]  # type: ignore[attr-defined]


def test_router_urls_the_policy_refuses_keep_extract_when_no_search_backend_is_offered() -> None:
    merged = _merge(_urls("http://10.0.0.1/x"), url_check=public_only)
    assert (merged.tool, merged.providers) == ("extract", ("jina",))  # type: ignore[attr-defined]
    assert NOTE_EXTRACT not in merged.notes.ignored  # type: ignore[attr-defined]
    assert "10.0.0.1" in merged.notes.ignored[NOTE_EXTRACT_URLS]  # type: ignore[attr-defined]


def test_no_router_url_keeps_extract_when_no_search_backend_is_offered() -> None:
    merged = _merge(RouteDecision(tools=["extract"]))
    assert (merged.tool, merged.providers) == ("extract", ("jina",))  # type: ignore[attr-defined]
    assert merged.notes.ignored.get(NOTE_EXTRACT) != MSG_EXTRACT_NO_URL  # type: ignore[attr-defined]
