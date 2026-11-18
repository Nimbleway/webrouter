"""Vendor modes on the wire: merging a mode's parts into a payload, and what a backend offers a router.

When the payload is built (:func:`~web_router.providers._utils.build_payload`), a content replaces
the fields its wire writes ("highlights instead of text"), a tier or flag is merged into nested
objects, and the caller's ``provider_options`` still go last. Pinning one is :mod:`._pinning`.

A mode is refused, before anything is sent, when the caller set something it would overwrite:
a ``provider_options`` key its wire writes, a ``search_depth`` other than the one it stands
for, or an option (``search_depth``, ``effort``) that sets a field the mode writes to another value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar, Literal, Protocol

from web_router.constants_modes import (
    DEPTH_LEVELS,
    EFFORT_LEVELS,
    MODE_FIELD,
    MSG_PIN_DEPTH,
    MSG_PIN_OPTION,
    MSG_PIN_WRITES,
    PART_SEP,
)
from web_router.exceptions_typed import InvalidParameterError
from web_router.modes import Content, Flag, Slots, Tier, split_mode
from web_router.types import NormalizedRequest

SEARCH: Literal["search"] = "search"
PROBED = {"search_depth": DEPTH_LEVELS, "effort": EFFORT_LEVELS}
"""The options a mode's wire may collide with, and their levels: each is probed alone for the fields it decides."""


class ModeOwner(Protocol):
    """A provider instance, as the merge reads it: its name, its parts, and how it builds a payload."""

    name: ClassVar[str]
    tiers: ClassVar[Mapping[str, Tier]]
    contents: ClassVar[Mapping[str, Content]]
    flags: ClassVar[Mapping[str, Flag]]

    @property
    def _build_payload(self) -> Callable[..., dict[str, Any]]:
        """Called with a search request alone when a collision is probed; an extract's also takes its URLs."""


def merged(ours: dict[str, Any], theirs: Mapping[str, Any]) -> dict[str, Any]:
    """``theirs`` laid over ``ours`` at every depth: a nested object is merged, any other value replaced."""
    out = dict(ours)
    for key, value in theirs.items():
        nested = out.get(key)
        out[key] = merged(nested, value) if isinstance(nested, dict) and isinstance(value, Mapping) else value
    return out


def slots(owner: Any, mode: str) -> Slots:
    """``mode``, a canonical mode of ``owner``, in its slots."""
    return split_mode(owner.name, mode.split(PART_SEP), owner)


def _wires(owner: Any, found: Slots) -> tuple[dict[str, Any], dict[str, Any]]:
    """The content's wire, then the tier's and flags' merged into one."""
    content = owner.contents.get(found.depth) if found.depth else None
    laid: dict[str, Any] = {}
    for part in [found.tier, *found.flags]:
        if part:
            laid = merged(laid, (owner.tiers.get(part) or owner.flags[part]).wire)
    return dict(content.wire) if content else {}, laid


def with_mode(payload: dict[str, Any], owner: ModeOwner, request: NormalizedRequest) -> dict[str, Any]:
    """``payload`` with the request's mode merged in, before ``provider_options``; refused as the module says."""
    mode = getattr(request, "mode", None)
    if not mode:
        return payload
    found = slots(owner, mode)
    replaced, laid = _wires(owner, found)
    content = owner.contents.get(found.depth) if found.depth else None
    for field in {*replaced, *laid} & set(request.provider_options):
        raise InvalidParameterError(MODE_FIELD, MSG_PIN_OPTION.format(mode=mode, field=field))
    if content is not None:
        refuse_depth(mode, content.as_depth, getattr(request, "search_depth", None))
    _refuse_collisions(owner, request, mode, {**replaced, **laid}, content is not None)
    return merged({**payload, **replaced}, laid)


def _refuse_collisions(
    owner: ModeOwner, request: NormalizedRequest, mode: str, wire: dict[str, Any], has_content: bool
) -> None:
    """Refuse an option the caller set that changes a field the wire writes, to a value the wire does not send.

    Only the options the request has: an extract or an answer has neither, so nothing is probed.
    """
    probed = [option for option in PROBED if option in type(request).model_fields]
    if not probed:
        return
    bare = request.model_copy(update={"mode": None, "provider_options": {}, **dict.fromkeys(probed)})
    base = owner._build_payload(bare)
    for option in probed:
        value = getattr(request, option, None)
        if value is None or (has_content and option == "search_depth"):
            continue
        alone = owner._build_payload(bare.model_copy(update={option: value}))
        for field, sent in wire.items():
            decides = alone.get(field) != base.get(field) or _decides(owner, bare, option, field)
            if decides and alone.get(field) != sent:
                message = MSG_PIN_WRITES.format(mode=mode, field=field, option=option, value=value)
                raise InvalidParameterError(MODE_FIELD, message)


def _decides(owner: ModeOwner, bare: NormalizedRequest, option: str, field: str) -> bool:
    """Whether ``option`` decides ``field``: its levels send the field in more than one way (``medium`` included)."""
    seen = set()
    for level in PROBED[option]:
        try:
            seen.add(repr(owner._build_payload(bare.model_copy(update={option: level})).get(field)))
        except InvalidParameterError:
            continue  # a level this backend does not take
    return len(seen) > 1


def refuse_depth(mode: str, level: str, asked: str | None) -> None:
    """Refuse a ``search_depth`` other than ``level``, the one ``mode`` names or stands for."""
    if asked not in (None, level):
        raise InvalidParameterError(MODE_FIELD, MSG_PIN_DEPTH.format(mode=mode, depth=level, asked=asked))


def offers(owner: Any) -> tuple[str, ...]:
    """The modes a router may pick for ``owner``, its default left out.

    Unless the class sets ``offers`` itself: every tier at the default depth, then each depth
    level it serves and each content at the default tier, then each flag. A tier the class names
    as its ``default_tier`` is the default already, so it is never offered twice.
    """
    own = getattr(owner, "offers", None)
    if own is not None:
        return tuple(own)
    default = getattr(owner, "default_tier", None)
    mapped = SEARCH + "_depth" in getattr(owner, "supported_parameters", ())
    levels = [level for level in DEPTH_LEVELS if mapped and level in getattr(owner, "depth_levels", ())]
    tiers = [tier for tier in owner.tiers if tier != default]
    return (*tiers, *levels, *owner.contents, *owner.flags)
