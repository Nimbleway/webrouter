"""Pricing a mode: its own row, else its tier's plus its flags', else unknown -- never another mode's price."""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import partial
from typing import Any

import pytest
from pydantic import ValidationError

from web_router import prices, prices_modes, registry, registry_answer
from web_router.exceptions_typed import InvalidParameterError
from web_router.modes import Flag, ModeDeclarations, Tier, parse_id
from web_router.prices import Price

ACME = "https://acme.example/pricing"


def _row(mode: str | None, kind: str | None, price: float | None, **fields: Any) -> Price:
    unit = fields.pop("unit", "per_1k" if price is not None else "unknown")
    base = {"provider": "acme", "method": "search", "source_url": ACME, "checked": "2026-11-16"}
    return Price.model_validate(base | {"mode": mode, "kind": kind, "price_usd": price, "unit": unit} | fields)


ROWS = (
    _row("lite", "depth", 10.0, default=True),
    _row("deep", "depth", 20.0),
    _row("acme-turbo", "tier", 4.0),
    _row("acme-pro", "tier", 6.0, depth_assumed="deep"),
    _row("acme-full", "content", 30.0, depth_assumed="deep"),
    _row("acme-extras", "flag", 11.0, adds_usd=1.0),
    _row("acme-snippets", "flag", None),
    _row("acme-turbo+acme-extras", "tier", 4.5),
    _row("lite", "depth", None, method="answer", unit="token_billed", default=True),
    _row("acme-extras", "flag", 11.0, method="answer", adds_usd=1.0),
    _row("acme-pro", "tier", None, method="answer", unit="token_billed"),
)


@pytest.fixture
def acme(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    index = {row.key: row for row in ROWS} | {(r.provider, r.method, None): r for r in ROWS if r.default}
    monkeypatch.setattr(prices, "prices", lambda: ROWS)
    monkeypatch.setattr(prices, "_index", lambda: index)
    monkeypatch.setattr(prices_modes, "measured_cost", lambda method, provider: 0.002)
    prices_modes.declared.cache_clear()
    yield
    prices_modes.declared.cache_clear()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (None, 0.010),  # the default row
        ("lite", 0.010),
        ("acme-turbo", 0.004),  # a tier's own row
        ("acme-turbo+acme-extras", 0.0045),  # a canonical row wins over tier plus flag
        ("acme-extras+acme-turbo", 0.0045),  # read in any order
        ("acme-turbo+lite", 0.004),  # the default level is the depth a tier assumes when it assumes none
        ("acme-pro+deep", 0.006),  # depth_assumed matches
        ("acme-pro+acme-extras", None),  # acme-pro assumes deep, the call sends no depth
        ("acme-pro+deep+acme-extras", 0.007),  # the tier at the depth it assumes, plus the flag
        ("acme-turbo+lite+acme-extras", 0.005),  # the default level counts as no depth
        ("deep+acme-extras", 0.021),  # a depth's row plus the flag
        ("acme-turbo+deep", None),  # a tier at a depth it was not priced at
        ("acme-turbo+acme-full", None),
        ("acme-extras+acme-snippets", None),  # one flag adds an unknown amount
        ("acme-nope", None),
        ("fast", None),  # a level with no row: never another level's price
    ],
)
def test_the_pricing_order(acme: None, mode: str | None, expected: float | None) -> None:
    got = prices_modes.mode_price("acme", "search", mode)
    assert got == (None if expected is None else pytest.approx(expected))


def test_a_token_billed_row_is_estimated_at_its_default_mode_only(acme: None) -> None:
    assert prices_modes.mode_price("acme", "answer", None) == 0.002
    assert prices_modes.mode_price("acme", "answer", "acme-pro") is None


def test_a_flag_on_a_token_billed_default_has_no_price(acme: None) -> None:
    assert prices_modes.mode_price("acme", "answer", "lite+acme-extras") is None
    assert prices_modes.mode_price("acme", "search", "lite+acme-extras") == pytest.approx(0.011)  # per_1k control


def test_the_shipped_list_prices_the_old_spelling_as_the_new_one_with_a_warning() -> None:
    with pytest.warns(DeprecationWarning, match="tavily-ultra-fast"):
        old = prices.estimate("tavily", "search", ":ultra-fast")
    assert old == prices.estimate("tavily", "search", "tavily-ultra-fast") == 0.008
    assert prices_modes.mode_price("tavily", "search", ":ultra-fast") == 0.008
    assert prices_modes.mode_price("tavily", "search", "tavily-ultra-fast+deep") is None
    assert prices_modes.canonical_parts("tavily", "search", ["tavily-full-content", "lite"]) is None


def test_every_vendor_mode_row_says_what_it_is() -> None:
    rows = [row for row in prices.prices() if row.mode is not None]
    assert all(row.kind is not None for row in rows)
    assert {(r.provider, r.mode) for r in rows if r.kind == "content"} == {
        ("exa", "exa-highlights"),
        ("you", "you-full-page-cache"),
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": "acme-x", "kind": None},
        {"mode": None, "kind": "tier"},
        {"mode": "turbo", "kind": "tier"},
        {"mode": "acme-x", "kind": "depth"},
        {"mode": "lite", "kind": "effort"},
        {"mode": "acme-x", "kind": "tier", "adds_usd": 1.0},
        {"mode": "acme-x", "kind": "tier", "writes": "type"},
        {"mode": "acme-x", "kind": "flag", "depth_assumed": "lite"},
        {"mode": "acme-x", "kind": "content"},
        {"mode": "acme-x", "kind": "tier", "depth_assumed": "high"},
        {"mode": "acme-x", "kind": "flag", "adds_usd": float("nan")},
        {"mode": "acme-x", "kind": "flag", "adds_usd": -1.0},
    ],
)
def test_a_row_whose_mode_fields_do_not_fit_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="price row"):
        _row(fields.pop("mode"), fields.pop("kind"), 5.0, **fields)


def _registered() -> list[tuple[str, str, type[Any]]]:
    search = [(n, "search", registry.get_provider_class(n)) for n in sorted(registry._BUILTIN_PROVIDERS)]
    research = [(n, "research", registry.get_research_provider_class(n)) for n in registry._BUILTIN_RESEARCH_PROVIDERS]
    extract = [(n, "extract", registry.get_extract_provider_class(n)) for n in registry._BUILTIN_EXTRACT_PROVIDERS]
    answer = [
        (n, "answer", registry_answer.get_answer_provider_class(n)) for n in registry_answer._BUILTIN_ANSWER_PROVIDERS
    ]
    browse = [(n, "browse", registry.get_browse_provider_class(n)) for n in registry._BUILTIN_BROWSE_PROVIDERS]
    return search + research + extract + answer + browse


@pytest.mark.parametrize(("name", "method", "provider"), _registered())
def test_every_declared_part_has_a_price_row(name: str, method: str, provider: type[Any]) -> None:
    assert prices_modes.unpriced_parts(name, method, provider) == [], "add a row, `unknown` if unpublished"


def test_a_declared_part_with_no_row_is_reported(acme: None) -> None:
    declared = type(
        "AcmeProvider",
        (ModeDeclarations,),
        {"name": "acme", "tiers": {"acme-turbo": Tier(), "acme-ghost": Tier()}, "flags": {"acme-extras": Flag()}},
    )
    assert prices_modes.unpriced_parts("acme", "search", declared) == ["acme-ghost"]


def test_an_old_vendor_spelling_without_a_depth_mark_is_no_id() -> None:
    """``tavily:ultra-fast`` names no part: only ``@`` marks the benchmarks' old spelling, and ``fast`` is a level."""
    declared = partial(prices_modes.declared, method="search")
    with pytest.raises(InvalidParameterError, match="ultra-fast"):
        parse_id("tavily:ultra-fast", declared)
    assert parse_id("tavily:fast", declared).mode == "fast"
    assert parse_id("tavily:ultra-fast@deep", declared).mode == "tavily-ultra-fast+deep"


@pytest.mark.parametrize("row", [row for row in prices.prices() if row.method == "search" and row.setup])
def test_every_setup_a_price_row_names_parses_to_its_mode(row: prices.Price) -> None:
    ids = re.findall(r"`([^`]+)`", row.setup or "")
    assert ids, f"{row.provider} {row.mode}: name the setup as a backticked id"
    for text in ids:
        found = parse_id(text, partial(prices_modes.declared, method="search"))
        assert found.provider == row.provider
        assert found.mode in ({row.mode, None} if row.default else {row.mode}), f"{text} is not {row.mode}"
