"""The checks a mode declaration and a price row's mode fields pass, apart from ``modes.py`` so it stays small.

It imports neither ``modes`` nor ``prices``, so both may import it.
"""

from __future__ import annotations

import math
from typing import Any

from web_router.constants_modes import (
    DEPTH_LEVELS,
    EFFORT_LEVELS,
    KIND_CONTENT,
    KIND_DEPTH,
    KIND_EFFORT,
    KIND_FLAG,
    KIND_TIER,
    MSG_DECLARED_AS_DEPTH,
    MSG_DECLARED_NAMELESS,
    MSG_DECLARED_PREFIX,
    MSG_DECLARED_TWICE,
    MSG_ROW_MODE,
    ROW_ADDS,
    ROW_ADDS_VALUE,
    ROW_DEPTH,
    ROW_LEVEL,
    ROW_NO_KIND,
    ROW_VENDOR,
    VENDOR_SEP,
)

LEVELS_BY_KIND = {KIND_DEPTH: DEPTH_LEVELS, KIND_EFFORT: EFFORT_LEVELS}


def check_declarations(owner: type) -> None:
    """Refuse the parts ``owner`` itself declares unless each starts with its provider's name and sits in one slot."""
    own = {slot: owner.__dict__.get(slot) or {} for slot in ("tiers", "contents", "flags")}
    parts = [part for declared in own.values() for part in declared]
    if not parts:
        return
    name = getattr(owner, "name", None)
    if not isinstance(name, str):
        raise ValueError(MSG_DECLARED_NAMELESS.format(owner=owner.__name__))
    prefix = name + VENDOR_SEP
    for part in parts:
        if not part.startswith(prefix) or part == prefix:
            raise ValueError(MSG_DECLARED_PREFIX.format(owner=owner.__name__, part=part, prefix=prefix + part))
        if parts.count(part) > 1:
            raise ValueError(MSG_DECLARED_TWICE.format(owner=owner.__name__, part=part))
    for part, content in own["contents"].items():
        if content.as_depth not in DEPTH_LEVELS:
            message = MSG_DECLARED_AS_DEPTH.format(
                owner=owner.__name__, part=part, as_depth=content.as_depth, levels=DEPTH_LEVELS
            )
            raise ValueError(message)


def _row_problem(provider: str, mode: str | None, kind: str | None, depth_assumed: str | None) -> str | None:
    if (mode is None) != (kind is None):
        return ROW_NO_KIND
    if kind in LEVELS_BY_KIND and mode not in LEVELS_BY_KIND[kind]:
        return ROW_LEVEL.format(kind=kind, levels=LEVELS_BY_KIND[kind])
    prefix = provider + VENDOR_SEP
    if kind in (KIND_TIER, KIND_CONTENT, KIND_FLAG) and not str(mode).startswith(prefix):
        return ROW_VENDOR.format(kind=kind, prefix=prefix)
    may_assume = kind in (KIND_TIER, KIND_CONTENT)
    if (depth_assumed is not None and (not may_assume or depth_assumed not in DEPTH_LEVELS)) or (
        kind == KIND_CONTENT and depth_assumed is None
    ):
        return ROW_DEPTH
    return None


def check_row_mode(
    key: Any, kind: str | None, depth_assumed: str | None, adds_usd: float | None, writes: str | None = None
) -> None:
    """Refuse a price row whose ``kind``, ``depth_assumed``, ``adds_usd`` or ``writes`` do not fit its mode."""
    provider, _, mode = key
    problem = _row_problem(provider, mode, kind, depth_assumed)
    if problem is None and (adds_usd is not None or writes is not None) and kind != KIND_FLAG:
        problem = ROW_ADDS
    if problem is None and adds_usd is not None and not (math.isfinite(adds_usd) and adds_usd >= 0):
        problem = ROW_ADDS_VALUE
    if problem is not None:
        raise ValueError(MSG_ROW_MODE.format(key=key, problem=problem))
