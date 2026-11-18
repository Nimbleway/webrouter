"""The merge's ``tools`` and ``providers`` run backends only: a vendor mode candidate never picks the method or ranks
a backend there, since only ``attempts`` run a mode; a decider is offered modes and answers them as attempts."""

from __future__ import annotations

import pytest

from tests.candidates_helpers import Shown, clear_keys, set_keys
from tests.merge_helpers import STATIC
from web_router import Candidate, RouteDecision, RouteRequest, WebRouter
from web_router.routing.candidates import build_candidates
from web_router.routing.decider_question import question_for
from web_router.routing.merge import merge
from web_router.types import SearchRequest

MODE_ONLY_SEARCH = RouteRequest(
    prompt="q",
    candidates=(
        Candidate(tool="search", provider="exa", mode="exa-instant"),  # a cap dropped Exa's default
        Candidate(tool="answer", provider="perplexity"),
    ),
    deadline_s=1.0,
)


def test_a_method_left_with_only_modes_is_passed_over_for_one_with_a_backend() -> None:
    merged = merge(MODE_ONLY_SEARCH, RouteDecision(tools=["search", "answer"]), STATIC)
    assert (merged.tool, merged.providers) == ("answer", ("perplexity",))
    assert "search" in merged.notes.ignored


def test_a_mode_id_ranked_as_a_backend_is_noted_not_dropped_silently() -> None:
    req = RouteRequest(
        prompt="q",
        candidates=(Candidate(tool="search", provider="exa"), Candidate(tool="search", provider="exa", mode="lite")),
        deadline_s=1.0,
    )
    merged = merge(req, RouteDecision(providers={"search": ["exa:lite"]}), STATIC)
    assert merged.notes.ranked == ()
    assert "vendor mode" in merged.notes.ignored["search:exa:lite"]


def test_a_deciders_question_offers_modes_too() -> None:
    assert list(question_for(MODE_ONLY_SEARCH).options) == ["search:exa:exa-instant", "answer:perplexity"]


def test_a_chain_entry_pinned_to_a_mode_keeps_its_place_when_modes_are_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    set_keys(clear_keys(monkeypatch), "exa")
    pinned = {"auto": {"chain": {"search": ["exa", "exa:exa-instant"]}}}
    found = build_candidates(WebRouter(provider_config=pinned), {"search": SearchRequest(query="q")}, modes=True)
    entry = [c for c in found.candidates if c.id == "search:exa:exa-instant"]
    assert [(c.provider, c.mode) for c in entry] == [("exa:exa-instant", None)], "the pinned entry, not the mode"
    with WebRouter(provider_config=pinned, router=Shown(), route_modes=True) as wr:
        assert wr.plan("q", tool="search").providers == {"search": ("exa", "exa:exa-instant")}
