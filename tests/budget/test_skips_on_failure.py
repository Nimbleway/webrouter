"""A call whose members failed and whose budget skipped the rest says so, so a cap never reads as an outage."""

from __future__ import annotations

import pytest

from tests.budget.conftest import Chain, Table
from web_router import AllProvidersFailedError, AsyncWebRouter, Budget, BudgetExceededError, ProviderError, WebRouter

CAP = Budget(max_cost_usd=0.05)
"""Room for the first member at $0.04, not the second."""


def _setup(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.04, None), second=(0.04, None), third=(0.04, None))
    chain.raises["first"] = ProviderError("first", "read timed out", kind="timeout")


def _check(error: AllProvidersFailedError, chain: Chain) -> None:
    assert chain.called == ["first"]
    assert isinstance(error.failures["first"], ProviderError)
    for skipped in ("second", "third"):
        assert isinstance(error.failures[skipped], BudgetExceededError), f"{skipped} was skipped by the budget"
    [note] = error.__notes__
    assert "budget skipped" in note and "search:second" in note and "search:third" in note


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_failure_then_budget_skips_names_each_skip(chain: Chain, priced: Table) -> None:
    _setup(chain, priced)
    with pytest.raises(AllProvidersFailedError) as caught:
        WebRouter().search("q", provider=chain.auto(), budget=CAP)
    _check(caught.value, chain)


@pytest.mark.parametrize("tool", ["search"], indirect=True)
async def test_the_async_call_names_them_too(chain: Chain, priced: Table) -> None:
    _setup(chain, priced)
    async with AsyncWebRouter() as router:
        with pytest.raises(AllProvidersFailedError) as caught:
            await router.search("q", provider=chain.auto(), budget=CAP)
    _check(caught.value, chain)


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_shared_budget_names_its_skips_once(chain: Chain, priced: Table) -> None:
    _setup(chain, priced)
    router = WebRouter()
    with router.budget(CAP) as shared, pytest.raises(AllProvidersFailedError) as caught:
        shared.search("q", provider=chain.auto())
    _check(caught.value, chain)
