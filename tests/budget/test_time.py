"""A per-call budget's time cap: the p90 rule, strict mode, and how research and browse wait under it.

As in :mod:`tests.budget.test_cost`, on the static order through the public
routers, with prices and p90s the test sets.
"""

from __future__ import annotations

import pytest

from tests.budget.conftest import Chain, Table, call
from web_router import BrowseTimeoutError, Budget, BudgetExceededError, DecisionHop, ResearchTimeoutError
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.providers._deadline import current_deadline
from web_router.routing import ledger

JOBS = ["research", "browse"]
TIMEOUT_ERROR = {"research": ResearchTimeoutError, "browse": BrowseTimeoutError}


def test_a_known_p90_that_does_not_fit_the_time_left_is_skipped(chain: Chain, priced: Table) -> None:
    priced.update(first=(None, 9.0), second=(None, 1.0))
    response = call(chain, Budget(time_limit_s=5.0))
    assert chain.called == ["second"]
    assert response.decision.hops == [DecisionHop(id=f"{chain.tool.name}:first", error="p90 9.0s > 5.0s left")]


def test_an_unknown_p90_runs_under_a_time_cap_in_its_place(chain: Chain, priced: Table) -> None:
    priced.update(second=(None, 1.0))  # first's p90 is unknown
    assert call(chain, Budget(time_limit_s=5.0)).provider == "first"
    assert chain.called == ["first"]


def test_strict_drops_an_unknown_p90(chain: Chain, priced: Table) -> None:
    priced.update(second=(None, 1.0))
    response = call(chain, Budget(time_limit_s=5.0, strict=True))
    assert chain.called == ["second"]
    assert response.decision.hops == [
        DecisionHop(id=f"{chain.tool.name}:first", error="no measured p90 under a strict time cap")
    ]


def test_strict_without_a_time_cap_drops_nothing(chain: Chain, priced: Table) -> None:
    assert call(chain, Budget(strict=True)).provider == "first"


def test_when_nothing_fits_the_time_left_the_call_raises_budget_exceeded(chain: Chain, priced: Table) -> None:
    priced.update(first=(None, 9.0), second=(None, 8.0), third=(None, 7.0))
    with pytest.raises(BudgetExceededError, match=r"p90 9\.0s > 5\.0s left"):
        call(chain, Budget(time_limit_s=5.0))
    assert chain.called == []


def test_every_attempt_runs_under_the_per_call_deadline_set_to_the_time_limit(
    chain: Chain, priced: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float] = []
    admit = ledger.Ledger.admit

    def spy(self: ledger.Ledger, attempt: ledger.Attempt) -> bool:
        deadline = current_deadline()
        seen.append(0.0 if deadline is None else deadline.seconds)
        return admit(self, attempt)

    monkeypatch.setattr(ledger.Ledger, "admit", spy)
    call(chain, Budget(time_limit_s=7.0))
    assert seen == [7.0]


# -- Billed jobs: research and browse ------------------------------------------


@pytest.mark.parametrize("tool", JOBS, indirect=True)
def test_a_job_waits_no_longer_than_the_time_left_and_hands_back_its_handle(chain: Chain, priced: Table) -> None:
    chain.stalls.add("first")
    with pytest.raises(TIMEOUT_ERROR[chain.tool.name]) as raised:
        call(chain, Budget(time_limit_s=0.2), timeout=600)
    assert raised.value.timeout == pytest.approx(0.2)
    assert raised.value.job_id.startswith("first"), "the handle names the job, to poll or cancel"
    assert chain.called == ["first"], "a run that started is never followed by a second"


@pytest.mark.parametrize("tool", JOBS, indirect=True)
def test_a_job_waits_no_longer_than_its_own_timeout_when_that_is_shorter(chain: Chain, priced: Table) -> None:
    chain.stalls.add("first")
    with pytest.raises(TIMEOUT_ERROR[chain.tool.name]) as raised:
        call(chain, Budget(time_limit_s=60), timeout=0.1)
    assert raised.value.timeout == pytest.approx(0.1)


@pytest.mark.parametrize("tool", JOBS, indirect=True)
def test_an_unknown_p90_job_starts_only_while_time_is_left(chain: Chain, priced: Table) -> None:
    chain.raises["first"] = ConfigurationError("first holds no key")
    chain.sleeps["first"] = 0.15  # spends the whole cap before it fails
    with pytest.raises(ProviderError):
        call(chain, Budget(time_limit_s=0.1))
    assert chain.called == ["first"], "second and third had no time left to start in"


@pytest.mark.parametrize("tool", JOBS, indirect=True)
def test_a_job_whose_known_p90_does_not_fit_never_starts(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.01, 400.0), second=(0.01, 30.0))
    response = call(chain, Budget(time_limit_s=60))
    assert chain.called == ["second"]
    assert response.decision.hops == [DecisionHop(id=f"{chain.tool.name}:first", error=_over(400.0, 60))]


def _over(p90: float, limit: float) -> str:
    return f"p90 {p90:.1f}s > {limit:.1f}s left"
