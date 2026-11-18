"""Vendor modes: the parts a provider declares, and the one grammar every mode id is written in.

An id is ``tool:provider[:mode]`` or ``provider[:mode]``. A mode is up to three slots joined with ``+``, always in
this order: a tier, a depth, then flags sorted. A slot left empty is the backend's default.

- A **tier** (:class:`Tier`) sets a vendor field (Exa's ``type``); a depth that writes the same field is refused.
- The **depth** slot holds a normalized level, bare (``lite``, ``fast``, ``deep``; ``low`` ... ``max`` for
  research), or a **content** (:class:`Content`), which replaces what depth sends and names the level it stands for.
- A **flag** (:class:`Flag`) writes a field no tier or other flag writes; a depth that sends that field another way
  is refused (``deepseek-content`` takes ``deep``, not ``lite``).

A vendor part is spelled with its provider's name first (``exa-instant``), so it can never be read as a normalized
level, and the parser learns each part's slot from the provider's declarations. Input in any order comes out canonical
(``exa-highlights+exa-instant`` is ``exa-instant+exa-highlights``). The old spellings parse for one release:
``tavily:ultra-fast@deep`` and ``:ultra-fast``. :mod:`web_router.providers._modes` merges a part's ``wire`` into
the request a pinned id sends.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Literal, NamedTuple, Protocol

from web_router._modes_checks import check_declarations
from web_router.constants_modes import (
    ID_SEP,
    KIND_FLAG,
    KIND_TIER,
    LEGACY_DEPTH_MARK,
    LEGACY_PRICE_MARK,
    LEVELS,
    MODE_FIELD,
    MSG_ID_SHAPE,
    MSG_MODE_EMPTY,
    MSG_MODE_TWICE,
    MSG_MODE_UNKNOWN,
    MSG_PRICE_LEGACY,
    PART_SEP,
    TOOLS,
    VENDOR_SEP,
)
from web_router.exceptions_typed import InvalidParameterError

ModeKind = Literal["tier", "content", "flag", "depth", "effort"]
"""What a price row's mode is: a declared tier, content or flag, or a normalized depth or effort level."""


@dataclass(frozen=True)
class Tier:
    """A vendor tier: ``wire`` is the payload change it makes, to fields depth never writes (Exa's ``type``)."""

    wire: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Content:
    """A content switch: ``wire`` replaces what depth sends, so it fills the depth slot as the level ``as_depth``."""

    as_depth: str
    wire: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Flag:
    """An add-on: ``wire`` sets one field (``writes`` names it), so it combines with any tier, and with a depth unless
    that depth sends the field another way; two flags on one field never combine."""

    wire: Mapping[str, Any] = field(default_factory=dict)
    writes: str | None = None


class Declared(Protocol):
    """What the parser reads a provider's parts from: a provider class, or :class:`Declarations`."""

    tiers: Mapping[str, Tier]
    contents: Mapping[str, Content]
    flags: Mapping[str, Flag]


@dataclass
class Declarations:
    """Parts declared outside a provider class (the price list's, a benchmark's); ``NO_MODES`` has none."""

    tiers: Mapping[str, Tier] = field(default_factory=dict)
    contents: Mapping[str, Content] = field(default_factory=dict)
    flags: Mapping[str, Flag] = field(default_factory=dict)


NO_MODES = Declarations()


class ModeDeclarations:
    """The parts a provider declares, none by default; a subclass's own are checked when the class is made."""

    tiers: ClassVar[Mapping[str, Tier]] = MappingProxyType({})
    contents: ClassVar[Mapping[str, Content]] = MappingProxyType({})
    flags: ClassVar[Mapping[str, Flag]] = MappingProxyType({})

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        check_declarations(cls)


class Slots(NamedTuple):
    """A mode by slot: its tier, its depth (a level or a content) and its flags, sorted."""

    tier: str | None
    depth: str | None
    flags: tuple[str, ...]

    @property
    def mode(self) -> str | None:
        """The canonical mode string, or ``None`` when every slot is empty."""
        return PART_SEP.join(p for p in (self.tier, self.depth, *self.flags) if p) or None


def _slot(provider: str, part: str, declared: Declared) -> str:
    if part in LEVELS or part in declared.contents:
        return "depth"
    if part in declared.tiers:
        return KIND_TIER
    if part in declared.flags:
        return KIND_FLAG
    known = sorted({*LEVELS, *declared.tiers, *declared.contents, *declared.flags})
    raise InvalidParameterError(MODE_FIELD, MSG_MODE_UNKNOWN.format(provider=provider, part=part), allowed=known)


def split_mode(provider: str, parts: Iterable[str], declared: Declared = NO_MODES) -> Slots:
    """``parts`` in their slots; :class:`InvalidParameterError` for an unknown part or a slot filled twice."""
    found: dict[str, list[str]] = {"tier": [], "depth": [], "flag": []}
    for part in parts:
        if not part:
            raise InvalidParameterError(MODE_FIELD, MSG_MODE_EMPTY.format(text=PART_SEP.join(parts)))
        found[_slot(provider, part, declared)].append(part)
    for slot, names in found.items():
        keys = [declared.flags[n].writes or n for n in names] if slot == KIND_FLAG else names
        if len(keys) > (len(set(keys)) if slot == KIND_FLAG else 1):
            raise InvalidParameterError(MODE_FIELD, MSG_MODE_TWICE.format(mode=PART_SEP.join(parts), slot=slot))
    tier, depth = (found[slot][0] if found[slot] else None for slot in ("tier", "depth"))
    return Slots(tier, depth, tuple(sorted(found[KIND_FLAG])))


def legacy_parts(provider: str, vendor: str, depth: str = "") -> list[str]:
    """The benchmarks' ``vendor@depth`` as parts: on tavily, ``ultra-fast@deep`` is ``tavily-ultra-fast``, ``deep``."""
    return ([provider + VENDOR_SEP + vendor] if vendor else []) + ([depth] if depth else [])


def parse_mode(provider: str, text: str | None, declared: Declared = NO_MODES) -> str | None:
    """``text``, a mode of ``provider``, canonical (``None`` for none); also the old ``x@deep`` and ``:x``."""
    if not text:
        return None
    priced = text.startswith(LEGACY_PRICE_MARK)
    if priced or LEGACY_DEPTH_MARK in text:
        vendor, mark, depth = text.removeprefix(LEGACY_PRICE_MARK).partition(LEGACY_DEPTH_MARK)
        if (priced and not vendor) or (mark and not depth):
            raise InvalidParameterError(MODE_FIELD, MSG_MODE_EMPTY.format(text=text))
        return split_mode(provider, legacy_parts(provider, vendor, depth), declared).mode
    return split_mode(provider, text.split(PART_SEP), declared).mode


class ModeId(NamedTuple):
    """A parsed id: the tool (``None`` when the id names none), the provider and the canonical mode."""

    tool: str | None
    provider: str
    mode: str | None

    def __str__(self) -> str:
        return ID_SEP.join(p for p in (self.tool, self.provider, self.mode) if p)


def parse_id(
    text: str, declared_for: Callable[[str], Declared] | None = None, *, tools: Iterable[str] = TOOLS
) -> ModeId:
    """``tool:provider[:mode]`` or ``provider[:mode]``, its mode canonical; ``declared_for(provider)`` gives the parts.

    Only an id with ``@`` is an old spelling (``tavily:ultra-fast@deep``); ``tavily:ultra-fast`` raises.
    """
    head, mark, depth = text.partition(LEGACY_DEPTH_MARK)
    parts = head.split(ID_SEP)
    tool = parts.pop(0) if len(parts) > 1 and parts[0] in set(tools) else None
    if not parts or len(parts) > 2 or not all(parts):
        raise InvalidParameterError(MODE_FIELD, MSG_ID_SHAPE.format(text=text))
    provider = parts[0]
    declared = NO_MODES if declared_for is None else declared_for(provider)
    mode = (parts[1] if len(parts) == 2 else "") + mark + depth
    return ModeId(tool, provider, parse_mode(provider, mode, declared))


def legacy_price_mode(provider: str, mode: str | None) -> str | None:
    """A price key's mode, the old ``:``-prefixed spelling (``:ultra-fast``) mapped to its part, with a warning."""
    if mode is None or not mode.startswith(LEGACY_PRICE_MARK):
        return mode
    new = provider + VENDOR_SEP + mode.removeprefix(LEGACY_PRICE_MARK)
    warnings.warn(MSG_PRICE_LEGACY.format(old=mode, new=new), DeprecationWarning, stacklevel=2)
    return new
