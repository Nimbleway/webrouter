"""Live checks that answer() behaves the same on every answer backend.

Each test runs only against the backends that map the option under test, as in
``test_integration_surface.py``.

Needs credentials; skips without them. Run with: make test-integration
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import pytest

from tests.conftest import billing_aware
from web_router import WebRouter
from web_router.constants import ANSWER_ENV_BY_PROVIDER, PERPLEXITY_OUTPUT_SEARCH_RESULTS
from web_router.registry_answer import available_answer_providers, get_answer_provider_class

pytestmark = pytest.mark.integration


QUESTION = "who won the 2022 FIFA World Cup final"
"""A settled fact every answer backend can find, so a wrong or empty answer is the code's fault."""


def _answerers(*required: str) -> list:
    """Every answer provider that maps ``required``, marked to skip without its keys."""
    return [
        pytest.param(
            name,
            marks=pytest.mark.skipif(
                not all(os.getenv(env_var) for env_var in env_vars), reason=f"{', '.join(env_vars)} not set"
            ),
            id=name,
        )
        for name, env_vars in ANSWER_ENV_BY_PROVIDER.items()
        if set(required) <= get_answer_provider_class(name).supported_parameters
    ]


def test_every_builtin_answer_provider_is_covered_here() -> None:
    assert set(ANSWER_ENV_BY_PROVIDER) == set(available_answer_providers())


@pytest.mark.parametrize("provider", _answerers())
def test_an_answer_comes_with_text_and_sources(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.answer(QUESTION)

    assert "argentina" in (response.answer or "").lower()
    assert response.citations
    assert all(citation.url.startswith("http") for citation in response.citations)


@pytest.mark.parametrize("provider", _answerers("include_domains"))
def test_an_answers_sources_honor_the_domain_filter(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.answer(QUESTION, include_domains=["wikipedia.org"])

    assert response.citations
    assert all("wikipedia.org" in citation.url for citation in response.citations)


@pytest.mark.parametrize("provider", _answerers("exclude_domains"))
def test_an_answers_sources_leave_out_excluded_domains(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.answer(QUESTION, exclude_domains=["wikipedia.org"])

    assert response.citations
    assert not any("wikipedia.org" in citation.url for citation in response.citations)


RECENCY_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}

STATED_DATES: dict[str, Any] = {
    "perplexity": lambda raw: [
        result.get("date")
        for item in raw.get("output") or []
        if item.get("type") == PERPLEXITY_OUTPUT_SEARCH_RESULTS
        for result in item.get("results") or []
    ],
    "you": lambda raw: [page.get("page_age") for page in (raw.get("results") or {}).get("web") or []],
}
"""Per backend, the publication dates its own payload states for the pages behind an answer."""

DATE_SLACK_DAYS = 7
"""A page indexed in the window can carry a publication date a little before it."""


@pytest.mark.parametrize("provider", [p for p in _answerers("recency") if p.id in STATED_DATES])
def test_recency_keeps_dated_sources_in_the_window(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.answer("what is the latest news about the James Webb Space Telescope", recency="month")

    dates = [d for payload in response.raw or [] for d in STATED_DATES[provider](payload) if d]
    floor = (date.today() - timedelta(days=RECENCY_DAYS["month"] + DATE_SLACK_DAYS)).isoformat()
    assert dates, "the backend stated no dates, so the window cannot be checked"
    assert all(d[:10] >= floor for d in dates), f"pages older than the window: {sorted(dates)[:3]}"


@pytest.mark.parametrize("provider", _answerers("recency"))
def test_recency_is_accepted(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.answer("what is the latest news about the James Webb Space Telescope", recency="week")

    assert response.answer
