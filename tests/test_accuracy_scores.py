"""A router's accuracy scores: checked once, against the registry and each backend's modes; and who has one."""

from __future__ import annotations

from typing import Any

import pytest

from tests.candidates_helpers import clear_keys, one
from web_router import WebRouter
from web_router.exceptions import ConfigurationError
from web_router.routing.accuracy import accuracy_scores, router_scores, score_of, shipped_scores
from web_router.routing.candidates import build_candidates
from web_router.routing.contract import Candidate
from web_router.types import SearchRequest


def test_scores_are_kept_per_method_by_backend_or_mode() -> None:
    scores = {"search": {"exa": 0.161, "exa:exa-deep": 0.224}, "answer": {"perplexity": 1}}
    assert accuracy_scores(scores) == {"search": {"exa": 0.161, "exa:exa-deep": 0.224}, "answer": {"perplexity": 1.0}}
    assert accuracy_scores(None) is None


@pytest.mark.parametrize(
    ("scores", "says"),
    [
        ([], "at the top level"),
        ({"live_data": {}}, "not a routed method"),
        ({"search": ["exa"]}, "at search"),
        ({"search": {1: 0.5}}, "at search"),
        ({"search": {"nope": 0.5}}, "'nope' for search, not one of its backends"),
        ({"extract": {"exa-mcp": 0.5}}, "'exa-mcp' for extract"),
        ({"search": {"exa:exa-depp": 0.5}}, "'exa-depp' for exa, which it does not offer"),
        ({"search": {"exa:": 0.5}}, "mode '' for exa, which it does not offer"),
        ({"search": {"exa:lite+exa-instant": 0.5}}, "is spelled 'exa-instant+lite'"),
        ({"search": {"exa": 1.5}}, "at 1.5"),
        ({"search": {"exa": -0.1}}, "at -0.1"),
        ({"search": {"exa": float("nan")}}, "at nan"),
        ({"search": {"exa": True}}, "at True"),
        ({"search": {"exa": "0.5"}}, "at '0.5'"),
    ],
)
def test_scores_that_name_nothing_real_or_are_not_shares_are_refused(scores: Any, says: str) -> None:
    with pytest.raises(ConfigurationError, match=says.replace("+", r"\+").replace("(", r"\(")):
        accuracy_scores(scores)


def test_a_file_is_read_as_the_same_shape(tmp_path: Any) -> None:
    path = tmp_path / "scores.json"
    path.write_text('{"search": {"exa": 0.3}}', encoding="utf-8")
    assert accuracy_scores(path) == accuracy_scores(str(path)) == {"search": {"exa": 0.3}}
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    for bad in (tmp_path / "bad.json", tmp_path / "missing.json"):
        with pytest.raises(ConfigurationError, match="could not read"):
            accuracy_scores(bad)


def test_a_candidate_is_scored_by_backend_or_mode_and_only_when_it_is_the_measured_backend() -> None:
    scores = accuracy_scores({"search": {"exa": 0.1, "exa:exa-deep": 0.3}})
    assert score_of(scores, Candidate(tool="search", provider="exa")) == 0.1
    assert score_of(scores, Candidate(tool="search", provider="exa", mode="exa-deep")) == 0.3
    assert score_of(scores, Candidate(tool="search", provider="exa", mode="exa-instant")) is None
    assert score_of(scores, Candidate(tool="answer", provider="exa")) is None
    assert score_of(scores, Candidate(tool="search", provider="exa", measured=False)) is None
    assert score_of(None, Candidate(tool="search", provider="exa")) is None


def test_a_keyless_tier_or_a_call_with_provider_options_is_not_the_measured_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_keys(monkeypatch).setenv("EXA_API_KEY", "test-key")
    router = WebRouter(provider_config={"auto": {"chain": {"search": [("tavily", {"allow_keyless": True}), "exa"]}}})
    found = build_candidates(router, {"search": SearchRequest(query="q")})
    assert (one(found, "search:tavily").measured, one(found, "search:exa").measured) == (False, True)
    optioned = build_candidates(router, {"search": SearchRequest(query="q", provider_options={"type": "fast"})})
    assert one(optioned, "search:exa").measured is False, "options the benchmark never sent"


def test_the_shipped_scores_are_search_and_answer_backends_as_the_benchmark_measured_them() -> None:
    shipped = shipped_scores()
    assert set(shipped) == {"search", "answer"} and all(shipped.values())
    assert accuracy_scores(shipped) == shipped, "every key a real backend, every score a share"
    assert not any(":" in key for keys in shipped.values() for key in keys), "no vendor mode is measured"
    assert shipped["answer"]["perplexity"] == pytest.approx(0.953333)


def test_a_router_holds_the_shipped_scores_only_at_accurate_with_none_of_its_own() -> None:
    assert router_scores("accurate", None) == shipped_scores()
    assert router_scores("accurate", {"search": {"exa": 0.5}}) == {"search": {"exa": 0.5}}
    assert router_scores(None, None) is None and router_scores("cheap", None) is None
