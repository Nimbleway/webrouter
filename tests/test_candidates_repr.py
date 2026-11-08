"""What a candidate set shows in its ``repr``: the candidates, never the requests they were priced on."""

from __future__ import annotations

import pytest

from tests.candidates_helpers import clear_keys
from web_router import WebRouter
from web_router.routing.candidates import build_candidates
from web_router.types import ExtractRequest


def test_a_candidate_sets_repr_holds_no_part_of_a_urls_path_or_query(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_keys(monkeypatch)
    secret = "https://example.com/private/report?token=SECRET123"
    found = build_candidates(WebRouter(), {"extract": ExtractRequest(urls=[secret])})
    assert found.requests["extract"].urls == [secret], "kept for pricing, just not shown"
    shown = repr(found)
    assert "SECRET123" not in shown and "/private/report" not in shown
