"""The candidate builder and a backend that cannot be built for an unexpected reason: dropped, never vanished."""

from __future__ import annotations

import pytest

from tests.candidates_helpers import clear_keys, set_keys
from tests.conftest import FakeProvider
from web_router import WebRouter, registry
from web_router.routing.candidates import build_candidates, default_requests
from web_router.types import SearchRequest


class Broken(FakeProvider):
    name = "broken"

    def __init__(self, **kwargs: object) -> None:
        raise RuntimeError("its SDK changed under it")


def test_a_member_that_raises_anything_else_when_built_is_dropped_with_what_it_raised() -> None:
    registry.register_provider("broken", Broken)
    registry.register_provider("fake-ok", FakeProvider)
    try:
        router = WebRouter(provider_config={"auto": {"chain": {"search": ["broken", "fake-ok"]}}})
        found = build_candidates(router, {"search": SearchRequest(query="q")})
    finally:
        registry.unregister_provider("broken")
        registry.unregister_provider("fake-ok")
    assert found.dropped["search:broken"] == "auto could not build it: it raised RuntimeError"
    assert "its SDK" not in found.dropped["search:broken"], "the class only: a message may carry anything"
    assert [c.id for c in found.candidates] == ["search:fake-ok"]


def test_a_plain_list_chain_naming_other_methods_backends_leaves_those_methods_without_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_keys(clear_keys(monkeypatch), "exa", "tavily")
    router = WebRouter(provider_config={"auto": {"chain": ["exa", "tavily"]}})
    found = build_candidates(router, default_requests("who won the cup"))
    assert {c.tool for c in found.candidates} >= {"search"}
    assert "research" not in {c.tool for c in found.candidates}
    assert found.dropped["research:tavily"] == "not a backend for this method"
    plan = router.plan("who won the cup")
    assert plan.tool == "search" and set(plan.providers["search"]) == {"exa", "tavily"}
