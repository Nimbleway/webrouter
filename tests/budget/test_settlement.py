"""What an attempt ends up charged: nothing when it never started, the backend's reported cost when it states one.

Through the public routers, on the fake chain of :mod:`tests.budget.conftest`, with prices the test sets.
"""

from __future__ import annotations

import httpx
import pytest

from tests.budget.conftest import Chain, Table, call, scoped
from web_router import Budget, WebRouter
from web_router.constants_cost import COST_REPORTED
from web_router.exceptions import ConfigurationError, ProviderError

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
