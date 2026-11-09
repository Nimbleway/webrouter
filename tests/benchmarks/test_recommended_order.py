"""The provider scan's recommended ``auto`` order: route-eval's ranking over a scan, and the report section for it."""

from __future__ import annotations

from types import ModuleType

from tests.benchmarks.conftest import SCAN_SETUP as SETUP
from tests.benchmarks.conftest import route_question, route_row
from tests.benchmarks.conftest import scan_row as _row


def test_the_recommended_auto_order_ranks_plain_backends_and_says_which_the_run_tells_apart(
    scan: ModuleType,
) -> None:
    """A suggestion, opt-in: plain backends by accuracy, a mode or depth left out, a paste-ready chain.

    `close` answers 38 of 40 against `best`'s 40, inside its interval; `far`
    answers 20, well outside it; `best@deep` and `best:turbo` are setups, not
    chain members, however well they score.
    """
    counts = {"best": 40, "close": 38, "far": 20, "best@deep": 40, "best:turbo": 40, "silent": 0}
    rows = []
    for provider, correct in counts.items():
        for i in range(40):
            grade = None if provider == "silent" else ("correct" if i < correct else "incorrect")
            rows.append(_row(provider, f"q{i}", grade))
    summary = scan.summarize(rows, {**SETUP, "providers": list(counts)})

    assert scan.recommended_order(summary) == [("best", None), ("close", False), ("far", True)]

    report = scan.markdown(summary)
    section = report.split("## Recommended auto order")[1].split("\n## ")[0]
    assert "| 1 | `best` |" in section and "| best, within the noise floor |" in section
    assert "| 2 | `close` |" in section and "not shown apart" in section
    assert "| 3 | `far` |" in section and "behind" in section
    assert "best@deep" not in section and "best:turbo" not in section and "`silent`" not in section
    assert 'WebRouter(provider_config={"auto": {"chain": {"search": ["best", "close", "far"]}}})' in section
    assert "openai:gpt-4o" in section, "the suggestion names the one judge it rests on"
    assert "opt-in" in section and "pull request backed by a scan" in section
    assert report.index("## Overall") < report.index("## Recommended auto order") < report.index("## By topic")


def test_a_scan_where_nothing_was_served_suggests_no_order(scan: ModuleType) -> None:
    summary = scan.summarize([_row("nimble", "q0", None)], {**SETUP, "providers": ["nimble"]})

    assert scan.recommended_order(summary) == []
    assert "no order to suggest" in scan.markdown(summary)


def test_a_backend_that_fails_most_searches_ranks_by_what_it_got_right_not_by_the_few_it_served(
    scan: ModuleType,
) -> None:
    """The you-mcp case: 30 of 40 searches failed, and the 10 it served were all right -- 100% on those."""
    rows = [_row("flaky", f"q{i}", "correct" if i < 10 else None) for i in range(40)]
    rows += [_row("steady", f"q{i}", "correct" if i < 30 else "incorrect") for i in range(40)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["flaky", "steady"]})

    assert summary["overall"]["flaky"]["accuracy"] == 1.0
    assert [p for p, _ in scan.recommended_order(summary)] == ["steady", "flaky"]
    section = scan.markdown(summary).split("## Recommended auto order")[1].split("\n## ")[0]
    assert "| 2 | `flaky` | 25% (10/40) |" in section and "| 30 |" in section


def test_the_recommended_order_never_names_auto_itself(scan: ModuleType) -> None:
    """As a member, `auto` would be a second auto on the default order, reaching unranked backends first."""
    rows = [_row(p, f"q{i}", "correct") for p in ("auto", "nimble") for i in range(20)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["auto", "nimble"]})

    assert [p for p, _ in scan.recommended_order(summary)] == ["nimble"]


def test_a_backend_that_answered_every_question_wrong_is_ranked_last_not_dropped(scan: ModuleType) -> None:
    rows = [_row("right", f"q{i}", "correct") for i in range(20)] + [
        _row("wrong", f"q{i}", "incorrect") for i in range(20)
    ]
    summary = scan.summarize(rows, {**SETUP, "providers": ["right", "wrong"]})

    assert [p for p, _ in scan.recommended_order(summary)] == ["right", "wrong"]

    only_wrong = scan.summarize(
        [_row("wrong", f"q{i}", "incorrect") for i in range(20)], {**SETUP, "providers": ["wrong"]}
    )
    assert [p for p, _ in scan.recommended_order(only_wrong)] == ["wrong"]
    assert "no order to suggest" not in scan.markdown(only_wrong)


def test_the_recommended_order_is_route_evals_ranking_cost_breaking_ties_inside_the_noise_floor(
    scan: ModuleType,
) -> None:
    """One ranking in the repo: webiq answers one more of 100 than perplexity, inside the floor, at no listed price.

    You-mcp ties webiq, but is keyless, so it goes last however well it scores.
    """
    counts = {"webiq": 90, "perplexity": 89, "brave": 70, "you-mcp": 90}
    rows = [
        _row(p, f"q{i}", "correct" if i < correct else "incorrect") for p, correct in counts.items() for i in range(100)
    ]
    summary = scan.summarize(rows, {**SETUP, "providers": list(counts)})

    order = scan.recommended_order(summary)
    assert [p for p, _ in order] == ["perplexity", "webiq", "brave", "you-mcp"], "priced first in the floor"
    labels = dict(order)
    assert labels["perplexity"] is None and labels["webiq"] is None, (
        "both are best within the floor: the cheaper one first is not called best over the more accurate one"
    )
    assert labels["brave"] is True
    assert labels["you-mcp"] is None, "as accurate as the best, kept last as keyless, is still marked best"
    section = scan.markdown(summary).split("## Recommended auto order")[1].split("\n## ")[0]
    assert "noise floor" in section and "list price" in section


def test_a_scan_leaves_out_rate_limit_or_quota_failures_and_suggests_route_evals_order_on_the_same_rows(
    scan: ModuleType, route_eval_rank: ModuleType
) -> None:
    """`limited` answers all 20 it reached; its other 20 failed on a rate limit or quota, which is no miss.

    Counted as misses, `limited` would score 50% and fall behind `steady`'s 85%.
    """
    errors = {"rate_limit": "HTTP 429: too many requests", "quota": "HTTP 402"}
    scan_rows, questions = [], []
    for i in range(40):
        error = None if i < 20 else ("rate_limit" if i % 2 else "quota")
        steady = "correct" if i < 34 else "incorrect"
        scan_rows += [
            _row("limited", f"q{i}", "correct" if error is None else None, error=errors.get(error or "")),
            _row("steady", f"q{i}", steady),
        ]
        questions.append(
            route_question(
                route_eval_rank,
                route_row("limited", None if error else "correct", qid=f"q{i}", error=error),
                route_row("steady", steady, qid=f"q{i}"),
            )
        )
    summary = scan.summarize(scan_rows, {**SETUP, "providers": ["limited", "steady"]})

    expected = route_eval_rank.recommended(route_eval_rank.backend_stats(questions), 0.0)
    assert [p for p, _ in scan.recommended_order(summary, 0.0)] == expected == ["limited", "steady"]
    assert summary["ranking"]["limited"]["excluded"] == 20
    section = scan.markdown(summary).split("## Recommended auto order")[1].split("\n## ")[0]
    assert "| Rate limit or quota |" in section
    assert "| 1 | `limited` | 100% (20/20) |" in section and "| 0 | 20 |" in section, "failed 0, left out 20"


def test_the_latency_tie_break_counts_every_served_row_as_route_eval_does(scan: ModuleType) -> None:
    """A search the judge failed to grade was served: its latency counts, as in route-eval's median.

    Both answer 10 of 20 at no listed price. `zeta`'s graded rows took 1.0s and its ungraded ones 0.1s,
    a 0.55s median over served rows; `alpha` took 0.8s. On graded rows alone `zeta` would be 1.0s, second.
    """
    rows = [_row("zeta", f"q{i}", "correct", latency=1.0) for i in range(10)]
    rows += [_row("zeta", f"q{i}", None, latency=0.1, error="timed out", failed_at="judge") for i in range(10, 20)]
    rows += [_row("alpha", f"q{i}", "correct" if i < 10 else "incorrect", latency=0.8) for i in range(20)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["alpha", "zeta"]})

    assert summary["ranking"]["zeta"]["latency_p50"] == 0.55
    assert [p for p, _ in scan.recommended_order(summary, 0.0)] == ["zeta", "alpha"]


def test_a_backend_counted_on_too_few_questions_is_listed_apart_not_ranked_as_route_eval_does(
    scan: ModuleType, route_eval_rank: ModuleType
) -> None:
    """`lucky` answers all 19 it was asked, `steady` 15 of 20: ranked, `lucky` would come first.

    Below route-eval's minimum, it is left out of the order and the chain, and listed after them, as
    route-eval and ``chains.json`` list it.
    """
    minimum = route_eval_rank.MIN_QUESTIONS
    rows = [_row("steady", f"q{i}", "correct" if i < 15 else "incorrect") for i in range(minimum)]
    rows += [_row("lucky", f"q{i}", "correct") for i in range(minimum - 1)]
    summary = scan.summarize(rows, {**SETUP, "providers": ["lucky", "steady"]})

    assert route_eval_rank.too_few(summary["ranking"]) == ["lucky"]
    assert [p for p, _ in scan.recommended_order(summary, 0.0)] == ["steady"]
    section = scan.markdown(summary).split("## Recommended auto order")[1].split("\n## ")[0]
    assert "| 1 | `steady` |" in section
    assert "| too few questions | `lucky` | 100% (19/19) |" in section
    assert section.index("`steady`") < section.index("| too few questions | `lucky`")
    assert 'WebRouter(provider_config={"auto": {"chain": {"search": ["steady"]}}})' in section


def test_a_scan_where_every_backend_was_counted_on_too_few_questions_lists_them_and_suggests_no_order(
    scan: ModuleType,
) -> None:
    summary = scan.summarize([_row("lucky", "q0", "correct")], {**SETUP, "providers": ["lucky"]})

    assert scan.recommended_order(summary) == []
    section = scan.markdown(summary).split("## Recommended auto order")[1].split("\n## ")[0]
    assert "| too few questions | `lucky` | 100% (1/1) |" in section
    assert "no order to suggest" in section and "WebRouter(" not in section
