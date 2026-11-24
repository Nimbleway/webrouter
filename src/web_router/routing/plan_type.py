"""A routed call worked out and not yet run: :class:`Plan`, what ``plan()`` returns and ``run()`` takes.

Experimental. Apart from :mod:`.contract` so each stays small.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from web_router._urls import shown
from web_router.constants_routing import PROMPT_PARAMS
from web_router.routing.contract import ToolName
from web_router.types_decision import DecisionRecord


@dataclass(frozen=True, kw_only=True)
class Plan:
    """A routed call, worked out and not yet run: the method, its options and the backends to try."""

    prompt: str
    """The prompt the plan was made for."""

    tool: ToolName
    """The method the call runs."""

    params: Mapping[str, Any] = field(default_factory=dict)
    """The options it runs with."""

    providers: Mapping[ToolName, Sequence[str]] = field(default_factory=dict)
    """Per method, the merged order of backends to try."""

    decision: DecisionRecord = field(default_factory=DecisionRecord)
    """How the plan was made: the same record a routed call's response carries."""

    def __repr__(self) -> str:
        """The plan without its prompt -- nor the query or task that repeat it -- and each URL as scheme and host."""
        params = {k: v for k, v in shown(self.params).items() if k not in PROMPT_PARAMS}
        return f"Plan(tool={self.tool!r}, params={params!r}, providers={dict(self.providers)!r})"
