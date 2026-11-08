"""The budget's research effort mapping: the cheapest measured effort within the noise floor of the best that fits.

The cases use the shipped table (``tools-effort-60``) and the shipped price
list, so a regenerated table that moves a pick shows up here.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from web_router import Budget, DecisionRecord, prices
from web_router.constants_params import PARAMS_SOURCE_BUDGET, PARAMS_SOURCE_CALLER
from web_router.routing import effort_table, params
from web_router.routing.params import EffortChoice, effort_misfit, map_effort

NIMBLE_PRICES = {"low": 0.025, "medium": 0.1, "high": 0.5}


def _table_with(monkeypatch: pytest.MonkeyPatch, provider: str, effort: str, **row: Any) -> None:
    """The shipped table with ``provider``'s ``effort`` measured as ``row``."""
    table = copy.deepcopy(effort_table.effort_table())
    table["research"].setdefault(provider, {"measured": {}})["measured"][effort] = row
    monkeypatch.setattr(effort_table, "effort_table", lambda: table)


def test_the_table_measures_what_the_cases_below_assume() -> None:
    nimble = params.measured_efforts("nimble")
    assert {e: s["accuracy"] for e, s in nimble.items()} == {"low": 0.85, "medium": 0.833333, "high": 0.866667}
    assert set(params.measured_efforts("parallel")) == {"low", "medium", "high", "max"}
    assert params.noise_floor() == pytest.approx(0.0118, abs=1e-4)


@pytest.mark.parametrize("budget", [None, Budget(), Budget(strict=True)])
def test_no_cap_changes_nothing(budget: Budget | None) -> None:
    # map_effort returns before it reads the provider when nothing is capped, so one provider covers them all.
    choice = map_effort("nimble", budget)
    assert (choice.effort, choice.source, choice.misfit) == (None, None, None)
    assert choice.record("nimble") == {
        "params": None,
        "params_source": None,
        "params_reason": choice.reason,
        "unmeasured": None,
    }


@pytest.mark.parametrize(
    ("cap", "effort"),
    [
        (0.03, "low"),  # only low fits
        (0.2, "low"),  # low (85.0%) beats medium (83.3%), so medium is never worth its price
        (10.0, "high"),  # high (86.7%) leads low by 1.7 points, more than the 1.2-point floor
    ],
)
def test_nimble_research_at_a_few_cost_caps(cap: float, effort: str) -> None:
    choice = map_effort("nimble", Budget(max_cost_usd=cap))
    assert (choice.effort, choice.source, choice.misfit) == (effort, PARAMS_SOURCE_BUDGET, None)
    assert choice.reason.startswith(f"effort {effort} (")


@pytest.mark.parametrize(
    ("cap", "effort"),
    [(0.01, "low"), (0.03, "medium"), (0.2, "high")],
)
def test_parallel_research_at_a_few_cost_caps(cap: float, effort: str) -> None:
    assert map_effort("parallel", Budget(max_cost_usd=cap)).effort == effort


def test_a_generous_budget_takes_a_cheaper_effort_within_the_floor_over_a_costlier_one() -> None:
    # Parallel high and max both score 83.3%; max costs three times as much.
    choice = map_effort("parallel", Budget(max_cost_usd=10.0))
    assert choice.effort == "high"
    assert effort_misfit("parallel", "max", Budget(max_cost_usd=10.0)) is None, "max fit, and lost on cost"


def test_with_nimbles_numbers_a_wider_floor_takes_low_over_high(monkeypatch: pytest.MonkeyPatch) -> None:
    # Low is 1.7 points under high: one question of 60. A floor that wide does not tell them apart.
    monkeypatch.setattr(params, "noise_floor", lambda: 0.02)
    assert map_effort("nimble", Budget(max_cost_usd=10.0)).effort == "low"


@pytest.mark.parametrize(("provider", "effort"), [("nimble", "low"), ("parallel", "high")])
def test_a_time_cap_leaves_out_the_efforts_whose_p90_is_over_it(provider: str, effort: str) -> None:
    # A 60s cap: Nimble medium (136s) and high (181s) are out; so is Parallel medium (74s).
    assert map_effort(provider, Budget(time_limit_s=60.0)).effort == effort


def test_the_tables_p90_wins_over_a_price_list_prior(monkeypatch: pytest.MonkeyPatch) -> None:
    # A price-list p90 that disagrees with the measured one must not move the pick.
    monkeypatch.setattr(prices, "estimate_latency", lambda *_: (1.0, 999.0))
    assert map_effort("nimble", Budget(time_limit_s=60.0)).effort == "low"


def test_when_no_measured_effort_fits_the_default_stays_and_says_whether_it_fits() -> None:
    choice = map_effort("nimble", Budget(max_cost_usd=0.01))
    assert (choice.effort, choice.source) == (None, None)
    assert choice.misfit is not None and "no known price" in choice.misfit
    assert map_effort("parallel", Budget(time_limit_s=30.0)).misfit == (
        "research:parallel at effort default has a p90 of 73.907s, over the 30s time limit"
    )


def test_the_callers_effort_always_stands_and_a_misfit_is_reported_not_downgraded() -> None:
    choice = map_effort("nimble", Budget(max_cost_usd=0.2), effort="high")
    assert (choice.effort, choice.source) == ("high", PARAMS_SOURCE_CALLER)
    assert choice.misfit == "research:nimble at effort high is estimated at $0.5, over the $0.2 cost cap"
    fits = map_effort("nimble", Budget(max_cost_usd=1.0), effort="high")
    assert (fits.effort, fits.misfit) == ("high", None)
    assert map_effort("nimble", None, effort="medium") == EffortChoice(
        effort="medium", source=PARAMS_SOURCE_CALLER, reason=fits.reason.replace("high", "medium")
    )


def test_an_unknown_price_never_fits_a_cost_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nimble max has no price. Measured as the best by far, it still loses under any cost cap...
    _table_with(monkeypatch, "nimble", "max", accuracy=1.0, latency_p90_s=10.0)
    assert map_effort("nimble", Budget(max_cost_usd=1000.0)).effort == "high"
    assert "no known price" in (effort_misfit("nimble", "max", Budget(max_cost_usd=1000.0)) or "")
    # ...and wins with no cost cap at all.
    assert map_effort("nimble", Budget(time_limit_s=1000.0)).effort == "max"


def test_an_unknown_p90_fits_a_time_cap_unless_the_budget_is_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    _table_with(monkeypatch, "nimble", "max", accuracy=1.0, latency_p90_s=None)
    assert map_effort("nimble", Budget(time_limit_s=60.0)).effort == "max"
    assert map_effort("nimble", Budget(time_limit_s=60.0, strict=True)).effort == "low"


@pytest.mark.parametrize("provider", ["perplexity", "linkup", "you"])
def test_an_unmeasured_backend_keeps_its_default_and_is_marked(provider: str) -> None:
    # Perplexity's rows mostly failed; Linkup ran at one effort only; You.com was not in the set.
    choice = map_effort(provider, Budget(max_cost_usd=10.0, time_limit_s=600.0))
    assert (choice.effort, choice.source, choice.unmeasured) == (None, None, True)
    record = DecisionRecord(**choice.record(provider), reason=choice.reason)
    assert record.unmeasured == [f"research:{provider}"]
    assert "measured efforts" in choice.reason


def test_a_choice_fills_the_decision_record() -> None:
    choice = map_effort("nimble", Budget(max_cost_usd=0.2))
    record = DecisionRecord(**choice.record("nimble"))
    assert (record.params, record.params_source, record.unmeasured) == ({"effort": "low"}, PARAMS_SOURCE_BUDGET, None)


def test_the_fit_check_reads_the_price_list() -> None:
    for effort, price in NIMBLE_PRICES.items():
        assert effort_misfit("nimble", effort, Budget(max_cost_usd=price)) is None, "a price equal to the cap fits"
        assert effort_misfit("nimble", effort, Budget(max_cost_usd=price * 0.99)) is not None
