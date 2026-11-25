"""route-eval's replay through the library's merge rule, and the routers it loads from a file -- all offline."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.benchmarks.conftest import route_question as _question
from tests.benchmarks.conftest import route_row as _row
from web_router import RouteDecision
from web_router.routing.merge import merge


def test_a_decision_is_merged_as_a_routed_call_would_merge_it(route_eval: ModuleType) -> None:
    q = _question(route_eval, _row("tavily", "correct"), _row("perplexity", "incorrect"), _row("brave", "correct"))
    decision = RouteDecision(providers={"search": ["tavily"]}, exclude={"search:perplexity": "slow"})
    outcome = route_eval.replay(lambda _: decision, q, route_eval.static_router("search"))
    assert outcome.served_by == "tavily" and not outcome.router_failed
    invalid = route_eval.replay(lambda _: {"decision": 42}, q, route_eval.static_router("search"))
    assert invalid.router_failed and invalid.served_by == "perplexity", "router_invalid: the static order"


def test_a_router_given_as_a_py_file_path_loads(route_eval: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "my.py").write_text(
        "from web_router import RouteDecision\n"
        "def pick(request):\n    return RouteDecision(providers={'search': ['perplexity']})\n"
    )
    router = route_eval.load_router(f"{tmp_path}/my.py:pick", "search")
    q = _question(route_eval, _row("tavily", "incorrect"), _row("perplexity", "correct"))
    outcome = route_eval.replay(router, q, route_eval.static_router("search"))
    assert outcome.served_by == "perplexity" and outcome.correct and not outcome.router_failed
    with pytest.raises(SystemExit, match="a router is static, oracle"):
        route_eval.load_router("x", "search")


def test_a_router_file_with_a_dataclass_loads(route_eval: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "picker.py").write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n\n"
        "from web_router import RouteDecision\n\n\n"
        "@dataclass\n"
        "class Picker:\n"
        "    first: str\n\n"
        "    def __call__(self, request):\n"
        "        return {'decision': RouteDecision(providers={'search': [self.first]}), 'cost_usd': 0.0}\n\n\n"
        "pick = Picker('perplexity')\n"
    )
    router = route_eval.load_router(f"{tmp_path}/picker.py:pick", "search")
    q = _question(route_eval, _row("tavily", "incorrect"), _row("perplexity", "correct"))
    assert route_eval.replay(router, q, route_eval.static_router("search")).served_by == "perplexity"


def test_the_replay_merges_with_the_librarys_own_function(route_eval: ModuleType) -> None:
    assert sys.modules["route_eval_merge"].merge is merge


def test_a_decision_ranking_no_candidate_fails_as_the_same_list_does(route_eval: ModuleType) -> None:
    q = _question(route_eval, _row("tavily", "incorrect"), _row("perplexity", "correct"))
    static = route_eval.static_router("search")
    as_list = route_eval.replay(lambda _: ["bogus"], q, static)
    as_decision = route_eval.replay(lambda _: RouteDecision(providers={"search": ["bogus"]}), q, static)
    assert as_list.router_failed and as_decision.router_failed
    assert as_decision.served_by == as_list.served_by == "perplexity"
    other = route_eval.replay(lambda _: RouteDecision(providers={"answer": ["bogus"]}), q, static)
    assert not other.router_failed, "a ranking for another method names nothing for this one"


def test_a_router_named_as_router_colon_name_is_built_and_asked(
    route_eval: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.decider_helpers import TOKEN, gemini, respond, serve

    monkeypatch.setenv("GEMINI_API_KEY", TOKEN)
    answers = [gemini("search:perplexity"), lambda req: respond({}, status=503)]
    server = serve(monkeypatch, lambda req: answers[len(server.requests) - 1](req))
    router = route_eval.load_router("router:gemini", "search")
    q = _question(route_eval, _row("tavily", "incorrect"), _row("perplexity", "correct"))
    outcome = route_eval.replay(router, q, route_eval.static_router("search"))
    assert outcome.served_by == "perplexity" and not outcome.router_failed
    failed = route_eval.replay(router, q, route_eval.static_router("search"))
    assert failed.router_failed and failed.router_error is not None and "503" in failed.router_error


def test_a_named_router_that_is_a_plain_or_async_callable_is_called(route_eval: ModuleType) -> None:
    from web_router.routing.registry import register_router, unregister_router

    async def pick(req: object) -> RouteDecision:
        return RouteDecision(providers={"search": ["perplexity"]})

    q = _question(route_eval, _row("tavily", "incorrect"), _row("perplexity", "correct"))
    outcome = route_eval.replay(
        route_eval.load_router("router:data-driven", "search"), q, route_eval.static_router("search")
    )
    assert not outcome.router_failed and outcome.router_error is None
    register_router("async_pick", lambda **config: pick)
    try:
        router = route_eval.load_router("router:async_pick", "search")
        outcome = route_eval.replay(router, q, route_eval.static_router("search"))
    finally:
        unregister_router("async_pick")
    assert not outcome.router_failed and outcome.served_by == "perplexity"


class _Learner:
    """A registered router with ``fit``, ``aroute`` and ``aclose``: notes the loop of each call."""

    def __init__(self) -> None:
        self.loops: list[object] = []
        self.closed = False

    def fit(self, questions: object) -> None:
        """Learns nothing."""

    async def aroute(self, req: object) -> RouteDecision:
        import asyncio

        self.loops.append(asyncio.get_running_loop())
        return RouteDecision(providers={"search": ["perplexity"]})

    async def aclose(self) -> None:
        self.closed = True


def test_a_named_router_keeps_its_fit_and_runs_on_one_loop_closed_at_exit(
    route_eval: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web_router.routing.registry import register_router, unregister_router

    learner, at_exit = _Learner(), []
    monkeypatch.setattr(sys.modules["route_eval_routers"].atexit, "register", at_exit.append)
    register_router("learner", lambda **config: learner)
    try:
        router = route_eval.load_router("router:learner", "search")
    finally:
        unregister_router("learner")
    assert router.fit == learner.fit
    q = _question(route_eval, _row("tavily", "incorrect"), _row("perplexity", "correct"))
    for _ in range(2):
        assert route_eval.replay(router, q, route_eval.static_router("search")).served_by == "perplexity"
    assert len(learner.loops) == 2 and learner.loops[0] is learner.loops[1]
    at_exit[0]()
    assert learner.closed and learner.loops[0].is_closed()  # type: ignore[attr-defined]


def test_a_named_router_on_a_set_without_question_text_is_refused(route_eval: ModuleType) -> None:
    from route_eval_sets import Question

    bare, asked = Question("q1", None, {}), Question("q2", "who won", {})
    with pytest.raises(SystemExit, match="--joined"):
        route_eval.check_prompts(("static", "router:gemini"), [asked, bare])
    route_eval.check_prompts(("router:gemini",), [asked])
    route_eval.check_prompts(("static", "oracle"), [bare])
