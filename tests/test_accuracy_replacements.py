"""Shipped scores belong to built-in implementations; caller scores also cover replacements."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import FakeAnswerProvider, FakeProvider
from tests.decider_helpers import AFixed, Fixed, reply
from web_router import DeciderRouter, PreferredRouter
from web_router.registry import is_custom, register_provider, unregister_provider
from web_router.registry_answer import register_answer_provider, unregister_answer_provider
from web_router.routing import decider_router, preferred
from web_router.routing.accuracy import score_of
from web_router.routing.contract import Candidate, RouteRequest

BACKENDS = [("search", "exa", "you"), ("search", "tavily", "you"), ("answer", "perplexity", "exa")]


@pytest.fixture(params=BACKENDS, ids=["exa-search", "tavily-search", "perplexity-answer"])
def backend(request: pytest.FixtureRequest) -> Any:
    method, name, other = request.param
    register = register_provider if method == "search" else register_answer_provider
    unregister = unregister_provider if method == "search" else unregister_answer_provider
    fake = FakeProvider if method == "search" else FakeAnswerProvider
    custom = type("Replacement", (fake,), {"name": name})
    try:
        yield method, name, other, lambda: register(name, custom), unregister
    finally:
        unregister(name)


@pytest.fixture
def seen_preferred(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    seen: list[Any] = []
    original = preferred.preferred_candidates

    def capture(candidates: Any, prefer: Any, scores: Any) -> Any:
        seen.append(candidates)
        return original(candidates, prefer, scores)

    monkeypatch.setattr(preferred, "preferred_candidates", capture)
    return seen


@pytest.fixture
def seen_questions(monkeypatch: pytest.MonkeyPatch) -> list[RouteRequest]:
    seen: list[RouteRequest] = []
    original = decider_router.question_for

    def capture(req: RouteRequest, scores: Any) -> Any:
        seen.append(req)
        return original(req, scores)

    monkeypatch.setattr(decider_router, "question_for", capture)
    return seen


def test_preferred_tracks_registration_changes_after_construction(backend: Any, seen_preferred: list[Any]) -> None:
    method, name, other, register, unregister = backend
    router = PreferredRouter("accurate")
    candidate = Candidate(tool=method, provider=name)
    req = RouteRequest(
        prompt="q", candidates=(candidate, Candidate(tool=method, provider=other)), pinned_tool=method, deadline_s=1
    )
    original = score_of(router.scores, candidate)
    assert original is not None and router.route(req).providers == {method: [name, other]}
    register()
    assert is_custom(method, name) and score_of(router.scores, candidate) is None
    assert router.route(req).providers == {method: [other, name]}
    unregister(name)
    assert not is_custom(method, name) and score_of(router.scores, candidate) == original
    assert router.route(req).providers == {method: [name, other]}
    assert [candidates[0].measured for candidates in seen_preferred] == [True, False, True]
    assert req.candidates[0].measured is True, "a fallback member receives the original eligible candidate"


@pytest.mark.parametrize("native_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("prefer", [None, "accurate"], ids=["service", "own"])
async def test_model_router_reads_live_shipped_scores_for_each_service_request(
    backend: Any, native_async: bool, prefer: Any, seen_questions: list[RouteRequest]
) -> None:
    method, name, _, register, unregister = backend
    fixed = AFixed(reply()) if native_async else Fixed(reply())
    router = DeciderRouter(fixed, prefer=prefer)
    candidate = Candidate(tool=method, provider=name)
    unscored = Candidate(tool="research", provider="parallel")
    req = RouteRequest(prompt="q", candidates=(candidate, unscored), prefer="accurate", deadline_s=1)
    for changed in [lambda: None, register, lambda: unregister(name)]:
        changed()
        if native_async:
            await router.aroute(req)
        else:
            router.route(req)
    descriptions = [question.options[candidate.id] for question in fixed.asked]
    assert descriptions[0] == descriptions[2] and "accuracy unknown" not in descriptions[0]
    assert descriptions[1].endswith(", accuracy unknown")
    assert [asked.candidates[0].measured for asked in seen_questions] == [True, False, True]
    assert req.candidates[0].measured is True
    assert all(asked.candidates[1].measured for asked in seen_questions), "built-in eligibility is unchanged"


def test_explicit_scores_for_custom_backends_are_preserved(backend: Any, seen_questions: list[RouteRequest]) -> None:
    method, name, _, register, _ = backend
    custom = {method: {name: 0.42}}
    preferred = PreferredRouter("accurate", accuracy=custom)
    fixed = Fixed(reply())
    model = DeciderRouter(fixed, prefer="accurate", accuracy=custom)
    register()
    candidate = Candidate(tool=method, provider=name)
    assert score_of(preferred.scores, candidate) == score_of(model.scores, candidate) == 0.42
    req = RouteRequest(prompt="q", candidates=(candidate,), deadline_s=1)
    model.route(req)
    assert fixed.asked[0].options[candidate.id].endswith(", accuracy 0.42")
    assert preferred.scores == model.scores == custom, "caller scores replace the whole shipped set"
    assert seen_questions[0].candidates[0].measured is True
    unmeasured = Candidate(tool=method, provider=name, measured=False)
    model.route(RouteRequest(prompt="q", candidates=(unmeasured,), deadline_s=1))
    assert seen_questions[1].candidates[0].measured is False
    assert fixed.asked[1].options[candidate.id].endswith(", accuracy unknown")


def test_without_scores_a_custom_candidate_keeps_its_eligibility(
    backend: Any, seen_questions: list[RouteRequest]
) -> None:
    method, name, _, register, _ = backend
    register()
    fixed = Fixed(reply())
    candidate = Candidate(tool=method, provider=name)
    req = RouteRequest(prompt="q", candidates=(candidate,), deadline_s=1)
    DeciderRouter(fixed).route(req)
    assert seen_questions[0] is req and seen_questions[0].candidates[0].measured is True
    assert "accuracy" not in fixed.asked[0].options[candidate.id]
