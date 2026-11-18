"""A planned run answers with what it took and cost, as the method called directly does. Offline.

The fake members carry no price, so the estimate is priced here at one rate
for ``third``, the member that answers: the run and ``search()`` must agree.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.plan_helpers import PROMPT, called_fixture  # noqa: F401  (fixture)
from tests.routing_helpers import wr as _wr
from web_router import AsyncWebRouter
from web_router.routing import metered

PRICE = 0.004


@pytest.fixture(autouse=True)
def third_is_priced(monkeypatch: pytest.MonkeyPatch, called: list[str]) -> None:
    monkeypatch.setattr(metered, "attempt_cost", lambda attempt: PRICE if attempt.provider == "third" else None)


def test_a_planned_run_carries_its_time_and_the_cost_search_reports() -> None:
    wr = _wr()
    response: Any = wr.run(wr.plan(PROMPT))
    assert response.elapsed_s is not None and response.elapsed_s >= 0
    assert (response.cost_usd, response.cost_source) == (PRICE, "estimated")
    direct = wr.search(PROMPT)
    assert direct.cost_usd == response.cost_usd, "the control: search() prices the same answer the same way"


async def test_an_async_planned_run_carries_them_too() -> None:
    response: Any = await _wr(AsyncWebRouter).run(PROMPT)
    assert response.elapsed_s is not None and response.cost_source == "estimated"
