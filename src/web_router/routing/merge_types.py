"""What the merge rule answers: :class:`Merged`, its :class:`MergeNotes`, and why a decision was set aside.

Experimental. Apart from :mod:`.merge` so each stays small.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from web_router.constants_merge import ROUTER_EMPTY, ROUTER_TIMEOUT
from web_router.constants_params import PARAMS_SOURCE_ROUTER
from web_router.routing.contract import ToolName
from web_router.routing.merge_urls import shown

FallbackReason = Literal["router_timeout", "router_error", "router_invalid"]
"""Why the static order was used instead of a decision."""


@dataclass(frozen=True, kw_only=True)
class MergeNotes:
    """How a decision was merged: what the decision record carries, and what of the decision was set aside."""

    ranked: tuple[str, ...] = ()
    """The router's ranking for the method, as candidate ids, unknown ones left out."""

    merged: tuple[str, ...] = ()
    """The order to try, as candidate ids."""

    excluded: Mapping[str, str] = field(default_factory=dict)
    """The candidates the router excluded, each with its reason."""

    ignored: Mapping[str, str] = field(default_factory=dict)
    """What of the decision was set aside, and why: an unknown id, an option as ``tool.key``, the exclusions."""

    fallback: FallbackReason | None = None
    """Why the static order was used instead of the decision; ``None`` when the decision was merged."""

    empty: bool = False
    """The router answered with a well-formed decision that decides nothing: the static order, and no failure."""

    reason: str | None = None
    """The router's own reason. Here, as in ``excluded``, any URL in a router's words shows as its scheme and host."""


@dataclass(frozen=True, kw_only=True)
class Merged:
    """A decision merged: the method to call, the backends to try in order, and the options a router set."""

    tool: ToolName
    providers: tuple[str, ...]
    """Backend names, in the order to try them."""

    params: Mapping[str, Any]
    """The router's options that passed every check. Empty, the caller's options and the budget's mapping apply.

    URLs are kept whole here, to be called with; the record and the ``repr`` show only their scheme and host."""

    notes: MergeNotes

    def __repr__(self) -> str:
        shown_params = shown(self.params)
        return (
            f"Merged(tool={self.tool!r}, providers={self.providers!r}, params={shown_params!r}, notes={self.notes!r})"
        )

    def record(self) -> dict[str, Any]:
        """The :class:`~web_router.DecisionRecord` fields the merge fills."""
        notes = self.notes
        return {
            "ranked": list(notes.ranked) or None,
            "merged": list(notes.merged),
            "excluded": dict(notes.excluded) or None,
            "reason": notes.fallback or (ROUTER_EMPTY if notes.empty else notes.reason),
            "router_timeout": True if notes.fallback == ROUTER_TIMEOUT else None,
            "params": shown(self.params) or None,
            "params_source": PARAMS_SOURCE_ROUTER if self.params else None,
        }
