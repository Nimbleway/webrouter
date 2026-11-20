"""What an attempt ends up charged: nothing when it never started, the backend's reported cost when it states one.

Through the public routers, on the fake chain of :mod:`tests.budget.conftest`, with prices the test sets.
"""

from __future__ import annotations

import math

import httpx
import pytest

from tests.budget.conftest import Chain, Table, call, scoped
from web_router import Budget, WebRouter
from web_router.constants_cost import COST_REPORTED
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.routing.attempts import Attempt
from web_router.routing.ledger import Ledger
from web_router.types import SearchRequest

SHARED = Budget(max_cost_usd=0.10)
_REQUEST = httpx.Request("GET", "https://api.test")


def _refused(status: int) -> ProviderError:
    error = httpx.HTTPStatusError("refused", request=_REQUEST, response=httpx.Response(status, request=_REQUEST))
    return ProviderError("first", "refused", original=error)


def test_an_attempt_that_never_started_is_charged_nothing_so_the_next_fits(chain: Chain, priced: Table) -> None:
    chain.raises["first"] = ConfigurationError("first holds no key")
    priced.update(first=(0.006, None), second=(0.006, None))
    response = call(chain, Budget(max_cost_usd=0.01))
    assert chain.called == ["first", "second"], "first's estimate was taken back, so second fits"
    assert [attempt.est_cost_usd for attempt in response.decision.attempts] == [0.0, 0.006]
    assert response.decision.estimated_cost_usd == 0.006


@pytest.mark.parametrize("tool", ["search"], indirect=True)
@pytest.mark.parametrize(
    "exc",
    [_refused(401), ProviderError("first", "no route", original=httpx.ConnectError("refused"))],
    ids=["auth-refused", "never-connected"],
)
def test_a_refusal_or_a_connection_never_made_frees_its_reservation(
    chain: Chain, priced: Table, exc: ProviderError
) -> None:
    chain.raises["first"] = exc
    priced.update(first=(0.02, None), second=(0.03, None))
    with WebRouter().budget(SHARED) as shared:
        scoped(chain, shared)
    assert chain.called == ["first", "second"]
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.03, 0.0)


@pytest.mark.parametrize("tool", ["search"], indirect=True)
@pytest.mark.parametrize("status", [502, 404, 410], ids=["gateway", "page-not-found", "page-gone"])
def test_a_server_error_or_a_page_gone_stays_charged(chain: Chain, priced: Table, status: int) -> None:
    chain.raises["first"] = _refused(status)  # the backend may have run, or billed finding the page gone
    priced.update(first=(0.02, None), second=(0.03, None))
    with WebRouter().budget(SHARED) as shared:
        scoped(chain, shared)
    assert shared.status().spent_usd == 0.05


@pytest.mark.parametrize("tool", ["search"], indirect=True)
def test_a_named_backend_that_never_started_is_charged_nothing(chain: Chain, priced: Table) -> None:
    chain.raises["first"] = _refused(403)
    priced.update(first=(0.02, None))
    with WebRouter().budget(SHARED) as shared, pytest.raises(ProviderError):
        shared.search("q", provider="first")
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.0, 0.0)


@pytest.mark.parametrize("tool", ["search", "answer"], indirect=True)  # the fakes that can state a cost
@pytest.mark.parametrize("reported", [0.004, 0.009], ids=["under-estimate", "over-estimate"])
def test_a_reported_cost_replaces_the_estimate_in_the_budget(
    chain: Chain, priced: Table, monkeypatch: pytest.MonkeyPatch, reported: float
) -> None:
    fake, entry = chain.tool.fake, chain.tool.entry
    real = getattr(fake, entry)

    def stated(self: object, *args: object) -> object:
        return real(self, *args).model_copy(update={"cost_usd": reported, "cost_source": COST_REPORTED})

    monkeypatch.setattr(fake, entry, stated)
    priced.update(first=(0.006, None))
    with WebRouter().budget(SHARED) as shared:
        response = scoped(chain, shared)
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (pytest.approx(reported), 0.0)
    assert response.decision.estimated_cost_usd == 0.006, "the record keeps the estimate"


@pytest.mark.parametrize("tool", ["search"], indirect=True)
@pytest.mark.parametrize("stated", [-0.05, math.nan, math.inf], ids=["negative", "nan", "infinite"])
def test_a_stated_cost_that_is_no_cost_leaves_the_estimate(
    chain: Chain, priced: Table, monkeypatch: pytest.MonkeyPatch, stated: float
) -> None:
    real = chain.tool.fake._respond

    def stated_cost(self: object, *args: object) -> object:
        return real(self, *args).model_copy(update={"cost_usd": stated, "cost_source": COST_REPORTED})

    monkeypatch.setattr(chain.tool.fake, "_respond", stated_cost)
    priced.update(first=(0.006, None))
    with WebRouter().budget(SHARED) as shared:
        scoped(chain, shared)
    assert (shared.status().spent_usd, shared.status().remaining_usd) == (0.006, pytest.approx(0.094))


def test_a_refund_for_another_candidate_leaves_the_open_attempt_charged(priced: Table) -> None:
    """A failure noted for a candidate that is not the open attempt refunds nothing: the open one may be running."""
    priced.update(first=(0.01, None))
    ledger = Ledger(Budget())
    ledger.admit(Attempt(tool="search", provider="first", request=SearchRequest(query="q")))
    assert ledger.refund_open("search:second") is None
    assert ledger.spent == 0.01
    assert ledger.refund_open("search:first") is not None
    assert ledger.spent == 0.0
