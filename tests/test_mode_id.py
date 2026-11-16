"""The mode id grammar: one canonical spelling whatever the input order, unknown parts refused, old spellings read."""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import ClassVar

import pytest

from web_router import prices_modes
from web_router.exceptions_typed import InvalidParameterError
from web_router.modes import Content, Flag, ModeDeclarations, ModeId, Tier, parse_id, parse_mode
from web_router.providers.base import SearchProvider


class Acme(ModeDeclarations):
    """A fake provider: one tier, one content and two flags (two, so their sorted order is visible)."""

    name: ClassVar[str] = "acme"
    tiers: ClassVar[Mapping[str, Tier]] = {"acme-turbo": Tier(wire={"type": "turbo"})}
    contents: ClassVar[Mapping[str, Content]] = {"acme-full": Content(as_depth="deep", wire={"full": True})}
    flags: ClassVar[Mapping[str, Flag]] = {
        "acme-snippets": Flag(wire={"snippets": True}),
        "acme-extras": Flag(wire={"extras": True}),
    }


def _acme(provider: str) -> type[Acme]:
    assert provider == "acme"
    return Acme


@pytest.mark.parametrize(
    ("parts", "canonical"),
    [
        (("acme-turbo", "deep", "acme-snippets", "acme-extras"), "acme-turbo+deep+acme-extras+acme-snippets"),
        (("acme-turbo", "acme-full", "acme-snippets"), "acme-turbo+acme-full+acme-snippets"),
        (("acme-extras", "lite"), "lite+acme-extras"),
    ],
)
def test_every_input_order_parses_to_one_canonical_id(parts: tuple[str, ...], canonical: str) -> None:
    for order in itertools.permutations(parts):
        assert parse_mode("acme", "+".join(order), Acme) == canonical
        assert parse_id("search:acme:" + "+".join(order), _acme) == ModeId("search", "acme", canonical)


def test_an_id_names_a_tool_or_not_and_prints_back_canonical() -> None:
    assert parse_id("acme", _acme) == ModeId(None, "acme", None)
    assert parse_id("acme:acme-snippets+acme-turbo", _acme) == ModeId(None, "acme", "acme-turbo+acme-snippets")
    assert str(parse_id("research:acme:high", _acme)) == "research:acme:high"
    assert parse_id("research:acme", _acme) == ModeId("research", "acme", None)


@pytest.mark.parametrize("mode", ["acme-nope", "turbo", "acme-turbo+snippets", "ultra"])
def test_an_unknown_part_is_refused(mode: str) -> None:
    with pytest.raises(InvalidParameterError) as raised:
        parse_mode("acme", mode, Acme)
    assert raised.value.field == "mode"
    assert raised.value.allowed is not None and "acme-turbo" in raised.value.allowed


@pytest.mark.parametrize(
    "mode",
    [
        "acme-turbo+acme-turbo",
        "lite+deep",
        "acme-full+deep",
        "acme-extras+acme-extras",
        "acme-turbo++lite",
        *(":", "turbo@", ":@deep"),  # an empty vendor or depth part in the old spellings
    ],
)
def test_a_slot_filled_twice_or_an_empty_part_is_refused(mode: str) -> None:
    with pytest.raises(InvalidParameterError):
        parse_mode("acme", mode, Acme)


@pytest.mark.parametrize(
    "text", ["", "search:", "a:b:c:d", "search:acme:lite:x", ":acme", "acme:acme-turbo@", "acme:@deep"]
)
def test_an_id_of_the_wrong_shape_is_refused(text: str) -> None:
    with pytest.raises(InvalidParameterError):
        parse_id(text, _acme)


def test_a_vendor_part_without_its_provider_prefix_is_refused_at_declaration() -> None:
    with pytest.raises(ValueError, match="starts with its provider's name"):

        class Bare(ModeDeclarations):
            name = "acme"
            tiers: ClassVar[Mapping[str, Tier]] = {"turbo": Tier()}


@pytest.mark.parametrize(
    ("declared", "message"),
    [
        ({"tiers": {"acme-x": Tier()}, "flags": {"acme-x": Flag()}}, "more than one"),
        ({"contents": {"acme-x": Content(as_depth="low")}}, "standing for"),
        ({"flags": {"acme-": Flag()}}, "starts with"),
    ],
)
def test_a_part_in_two_slots_or_a_content_off_the_depth_levels_is_refused(
    declared: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        type("Bad", (ModeDeclarations,), {"name": "acme", **declared})


def test_declaring_parts_needs_a_provider_name() -> None:
    with pytest.raises(ValueError, match="no provider name"):
        type("Nameless", (ModeDeclarations,), {"flags": {"acme-x": Flag()}})


def test_a_subclass_inherits_its_parents_parts_unchecked_against_its_own_name() -> None:
    proxy = type("AcmeProxy", (Acme,), {"name": "acme-proxy"})
    assert proxy.tiers == Acme.tiers


def test_every_provider_declares_nothing_by_default() -> None:
    assert (SearchProvider.tiers, SearchProvider.contents, SearchProvider.flags) == ({}, {}, {})


def test_the_benchmark_and_price_list_spellings_parse_to_the_canonical_mode() -> None:
    tavily = prices_modes.declared("tavily", "search")
    canonical = parse_mode("tavily", "deep+tavily-ultra-fast", tavily)
    assert canonical == "tavily-ultra-fast+deep"
    assert parse_id("tavily:ultra-fast@deep", lambda _: tavily) == ModeId(None, "tavily", canonical)
    assert parse_id("search:tavily:tavily-ultra-fast+deep", lambda _: tavily).mode == canonical
    assert parse_mode("tavily", ":ultra-fast", tavily) == parse_mode("tavily", "tavily-ultra-fast", tavily)
    assert parse_id("tavily@deep", lambda _: tavily) == ModeId(None, "tavily", "deep")


@pytest.mark.parametrize("pair", [("low", "medium"), ("medium", "low")])
def test_two_flags_that_set_one_field_are_refused_in_either_order(pair: tuple[str, str]) -> None:
    perplexity = prices_modes.declared("perplexity", "search")
    with pytest.raises(InvalidParameterError, match="flag slot twice"):
        parse_mode("perplexity", "+".join(f"perplexity-context-{size}" for size in pair), perplexity)
    one = parse_mode("perplexity", f"perplexity-context-{pair[0]}+perplexity-fast", perplexity)
    assert one == f"perplexity-fast+perplexity-context-{pair[0]}"


def test_tavilys_own_fast_tier_is_not_the_normalized_fast() -> None:
    tavily = prices_modes.declared("tavily", "search")
    assert parse_mode("tavily", "fast", tavily) == "fast"
    assert parse_mode("tavily", ":fast", tavily) == "tavily-fast"
