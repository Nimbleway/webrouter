"""The merge's ``tools`` and ``providers`` run backends only: a vendor mode candidate never picks the method or ranks
a backend there, since only ``attempts`` run a mode; a decider is offered modes and answers them as attempts."""

from __future__ import annotations

from tests.merge_helpers import STATIC
from web_router import Candidate, RouteDecision, RouteRequest
from web_router.routing.decider_question import question_for
from web_router.routing.merge import merge

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
