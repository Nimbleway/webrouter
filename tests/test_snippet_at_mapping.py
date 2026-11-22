"""Each backend's mapping fills ``description`` with a snippet; the result model keeps what it is given."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from web_router.providers.agentcore import AgentCoreProvider
from web_router.providers.linkup import LinkupProvider
from web_router.providers.seltz import SeltzProvider
from web_router.snippet import SNIPPET_LIMIT
from web_router.types import SearchRequest, SearchResult

PAGE = "word " * 3000


def _is_snippet_of(result: SearchResult, page: str) -> None:
    assert len(result.description) <= SNIPPET_LIMIT and result.description.endswith("…")
    assert page.startswith(result.description[:-1]), "the snippet is the head of the page"


def test_the_result_model_keeps_the_description_it_is_given() -> None:
    assert SearchResult(url="https://a.com", description=PAGE).description == PAGE


def test_nimbles_body_in_description_maps_to_a_snippet_and_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("nimble_python", reason="nimble extra not installed")
    from web_router.providers.nimble import NimbleProvider

    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    payload = {"results": [{"url": "https://a.com", "description": PAGE, "content": ""}]}
    result = NimbleProvider()._to_response(SearchRequest(query="q"), payload).results[0]

    assert result.content == PAGE
    _is_snippet_of(result, PAGE)


@pytest.mark.parametrize(
    ("to_result", "hit"),
    [
        (AgentCoreProvider._to_result, {"url": "https://a.com", "text": PAGE}),
        (LinkupProvider._to_result, {"url": "https://a.com", "content": PAGE}),
        (SeltzProvider._to_result, {"url": "https://a.com", "content": PAGE}),
    ],
    ids=["agentcore", "linkup", "seltz"],
)
def test_a_backend_with_no_snippet_gets_one_from_its_body(
    to_result: Callable[[dict[str, Any]], SearchResult], hit: dict[str, Any]
) -> None:
    result = to_result(hit)

    assert result.content == PAGE
    _is_snippet_of(result, PAGE)
