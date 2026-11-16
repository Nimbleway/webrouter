"""What a mode costs: the price list read in the grammar of :mod:`web_router.modes`.

A mode is priced in this order, and never at another mode's price:

1. the row for its canonical mode string;
2. else its tier's row, when the tier's ``depth_assumed`` is the mode's depth
   (an empty depth slot, or the backend's default level, is ``None``), plus each
   flag's ``adds_usd``;
3. else unknown, ``None``.

A row billed by tokens has no list price: it is estimated at the mean cost the
benchmark measured, at its default mode only, and is unknown at any other, a
flag on the default included. A
row whose figure is only a floor is unknown too, so a cost cap never trusts it.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import cache

from web_router import prices
from web_router._chains import measured_cost
from web_router.constants_budget import TOKEN_BILLED
from web_router.constants_modes import KIND_CONTENT, KIND_DEPTH, KIND_EFFORT, KIND_FLAG, KIND_TIER, PART_SEP
from web_router.constants_prices import PER_ONE
from web_router.exceptions_typed import InvalidParameterError
from web_router.modes import Content, Declarations, Declared, Flag, Slots, Tier, parse_mode, split_mode
from web_router.types import Provider, provider_name


@cache
def declared(provider: str, method: str) -> Declarations:
    """The vendor parts the price list names for ``(provider, method)``, in their slots by each row's ``kind``."""
    rows = [row for row in prices.prices() if row.provider == provider and row.method == method and row.mode]
    return Declarations(
        tiers={row.mode: Tier() for row in rows if row.kind == KIND_TIER and row.mode},
        contents={
            row.mode: Content(as_depth=row.depth_assumed or "") for row in rows if row.kind == KIND_CONTENT and row.mode
        },
        flags={row.mode: Flag(writes=row.writes) for row in rows if row.kind == KIND_FLAG and row.mode},
    )


def canonical_parts(provider: str, method: str, parts: Iterable[str]) -> str | None:
    """``parts`` as one canonical mode, or ``None`` when the price list does not name one of them."""
    try:
        return split_mode(provider, list(parts), declared(provider, method)).mode
    except InvalidParameterError:
        return None


def _row_price(row: prices.Price, provider: str, method: str, mode: str | None, flagged: bool = False) -> float | None:
    if row.unit == TOKEN_BILLED:
        default = mode is None or row.default
        return measured_cost(method, provider) if default and not flagged else None
    if row.estimate_is_floor:
        return None
    return row.estimate_usd


def _default_level(provider: str, method: str) -> str | None:
    row = prices.price(provider, method)
    return row.mode if row is not None and row.kind in (KIND_DEPTH, KIND_EFFORT) else None


def _base_price(provider: str, method: str, slots: Slots) -> float | None:
    base = Slots(slots.tier, slots.depth, ()).mode
    row = prices.price(provider, method, base)
    if row is not None and (slots.tier is None or slots.depth is not None):
        return _row_price(row, provider, method, base, flagged=bool(slots.flags))
    tier = prices.price(provider, method, slots.tier) if slots.tier else None
    if tier is None or tier.kind != KIND_TIER:
        return None
    default = _default_level(provider, method)
    assumed, depth = (None if d == default else d for d in (tier.depth_assumed, slots.depth))
    return _row_price(tier, provider, method, slots.tier, flagged=bool(slots.flags)) if assumed == depth else None


def mode_price(provider: str | Provider, method: str, mode: str | None) -> float | None:
    """The list price of one ``method`` call on ``provider`` at ``mode``, by the order above; ``None`` when unknown."""
    name = provider_name(provider)
    parts = declared(name, method)
    try:
        text = parse_mode(name, mode, parts)
        slots = split_mode(name, text.split(PART_SEP) if text else [], parts)
    except InvalidParameterError:
        return None
    row = prices.price(name, method, slots.mode)
    if row is not None:
        return _row_price(row, name, method, slots.mode)
    if slots.mode is None:
        return None
    base = _base_price(name, method, slots)
    adds = [_adds(prices.price(name, method, flag)) for flag in slots.flags]
    if base is None or None in adds:
        return None
    return base + sum(a for a in adds if a is not None)


def _adds(row: prices.Price | None) -> float | None:
    if row is None or row.adds_usd is None or row.unit not in PER_ONE:
        return None
    return row.adds_usd / PER_ONE[row.unit]


def unpriced_parts(provider: str, method: str, owner: Declared) -> list[str]:
    """The parts ``owner`` declares that have no price row, not even an ``unknown`` one: each needs a row."""
    parts = [*owner.tiers, *owner.contents, *owner.flags]
    return [part for part in parts if prices.price(provider, method, part) is None]
