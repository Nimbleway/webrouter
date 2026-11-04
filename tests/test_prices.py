"""The shipped price list: every built-in backend is priced or marked unknown, and PRICING.md's tables follow it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scripts.prices import BLOCK, PRICING_MD, regenerate, select
from web_router import prices, registry, registry_answer
from web_router.constants import PROVIDER_AUTO, PROVIDER_LOCAL, PROVIDER_MAX
from web_router.prices import Price

BUILTIN_BY_METHOD = {
    "search": registry._BUILTIN_PROVIDERS,
    "extract": registry._BUILTIN_EXTRACT_PROVIDERS,
    "run_template": registry._BUILTIN_TEMPLATE_PROVIDERS,
    "research": registry._BUILTIN_RESEARCH_PROVIDERS,
    "open_browser": registry._BUILTIN_BROWSER_PROVIDERS,
    "browse": registry._BUILTIN_BROWSE_PROVIDERS,
    "answer": registry_answer._BUILTIN_ANSWER_PROVIDERS,
}

# Routers over other backends, and the caller's own machine: nothing of their own to price.
EXEMPT = frozenset({PROVIDER_AUTO, PROVIDER_MAX, PROVIDER_LOCAL})


def _row(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "provider": "acme",
        "method": "search",
        "price_usd": 5.0,
        "unit": "per_1k",
        "source_url": "https://acme.example/pricing",
        "checked": "2026-11-04",
    }
    return base | fields


@pytest.mark.parametrize(
    ("provider", "method"),
    [
        (provider, method)
        for method, table in BUILTIN_BY_METHOD.items()
        for provider in sorted(table)
        if provider not in EXEMPT
    ],
)
def test_every_builtin_backend_has_a_price_row(provider: str, method: str) -> None:
    assert any(row.provider == provider and row.method == method for row in prices.prices()), (
        f"no price row for ({provider!r}, {method!r}): add one to src/web_router/data/prices.json, "
        "with unit 'unknown' if the vendor publishes no price, then run `make prices`"
    )


def test_every_builtin_backend_answers_a_call_that_sets_no_mode() -> None:
    unanswered = [
        (provider, method)
        for method, table in BUILTIN_BY_METHOD.items()
        for provider in table
        if provider not in EXEMPT and prices.price(provider, method) is None
    ]
    assert unanswered == []
    # Nimble's research default effort is the vendor's own: a row, priced unknown.
    assert prices.estimate("nimble", "research") is None


def test_estimate_is_the_cost_of_one_call() -> None:
    assert prices.estimate("tavily", "search") == pytest.approx(0.008)
    assert prices.estimate("tavily", "search", "fast") == pytest.approx(0.016)
    assert prices.estimate("nimble", "research", "low") == pytest.approx(0.025)
    assert prices.estimate("linkup", "extract") == pytest.approx(0.001)


def test_deep_search_with_full_content_costs_more_than_fast() -> None:
    # Deep sends full_content (Nimble) or full_page (You.com), billed per page on top of the depth price.
    for provider in ("nimble", "you"):
        deep = prices.estimate(provider, "search", "deep")
        fast = prices.estimate(provider, "search", "fast")
        assert deep is not None and fast is not None
        assert deep > fast


def test_a_call_that_sets_no_mode_gets_the_default_row() -> None:
    assert prices.estimate("nimble", "search") == prices.estimate("nimble", "search", "fast")
    assert prices.price("parallel", "research") == prices.price("parallel", "research", "medium")


def test_unknown_and_token_billed_have_no_estimate() -> None:
    assert prices.price("webiq", "search") is not None
    assert prices.estimate("webiq", "search") is None
    assert prices.price("perplexity", "research", "high") is not None
    assert prices.estimate("perplexity", "research", "high") is None
    assert prices.estimate("nimble", "research", "max") is None


def test_a_mode_with_no_row_is_unknown_not_another_modes_price() -> None:
    assert prices.price("linkup", "search", "lite") is None
    assert prices.estimate("brave", "search", "deep") is None
    assert prices.estimate("nobody", "search") is None


def test_browser_sessions_are_priced_per_minute() -> None:
    steel = prices.price("steel", "open_browser")
    assert steel is not None
    assert steel.unit == "per_browser_minute"
    assert steel.estimate_usd is not None
    assert steel.estimate_usd * 60 == pytest.approx(0.10, abs=1e-6)


@pytest.mark.parametrize(
    "fields",
    [
        {"unit": "unknown"},
        {"price_usd": None},
        {"price_usd": -1},
        {"source_url": None},
        {"checked": None},
        {"unit": "per_hour"},
        {"method": "crawl"},
        {"surprise": True},
    ],
)
def test_a_malformed_row_is_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Price.model_validate(_row(**fields))


def test_a_free_or_unknown_row_needs_no_source() -> None:
    # The same rows with a price to pay are refused: the exemption is for free and unknown rows only.
    Price.model_validate(_row(price_usd=0, source_url=None, checked=None))
    Price.model_validate(_row(price_usd=None, unit="unknown", source_url=None, checked=None))
    with pytest.raises(ValidationError):
        Price.model_validate(_row(price_usd=0.01, source_url=None, checked=None))
    with pytest.raises(ValidationError):
        Price.model_validate(_row(price_usd=0.01, unit="unknown", source_url=None, checked=None))


def test_rows_are_unique_with_one_default_each() -> None:
    keys = [row.key for row in prices.prices()]
    assert len(keys) == len(set(keys))
    no_mode = [(row.provider, row.method) for row in prices.prices() if row.mode is None or row.default]
    assert len(no_mode) == len(set(no_mode))


def test_pricing_md_tables_match_the_data() -> None:
    current = PRICING_MD.read_text(encoding="utf-8")
    assert regenerate(current, prices.prices()) == current, "PRICING.md is stale: run `make prices`."


def test_every_row_appears_in_exactly_one_pricing_md_table() -> None:
    text = PRICING_MD.read_text(encoding="utf-8")
    shown = [row.key for match in BLOCK.finditer(text) for row in select(match["spec"], prices.prices())]
    assert sorted(shown, key=str) == sorted((row.key for row in prices.prices()), key=str)
