"""The built-in ``data-driven`` router: a request's method, and search's backends, from tables shipped as a release.

Experimental. It needs no key and calls nothing: five regular expressions read the prompt
(:mod:`.features`), and the shipped table (:mod:`.table`) says which method the training requests with those
values were served by. It ranks the methods of that row, most common first. When the call then searches, it
orders the search backends a benchmark measured (:mod:`.providers`) along a cost or a speed line
(:mod:`.lines`), and names their modes in ``extra_modes`` so the call is shown them; every other backend runs
after, in ``auto``'s order. It is the routers' default, attached unless ``router=`` says otherwise, and it
declares ``route_tools=()``, so only
:meth:`~web_router.WebRouter.plan` and :meth:`~web_router.WebRouter.run` ask it.

Two methods are held back. ``browse`` is ranked only by a router built with ``browse=True``: a browser agent
is slow and billed, so a caller opts in. ``extract`` is ranked only when the call names a URL it may read.
``answer`` is not a method the table was trained on, so it is never picked.
"""

from __future__ import annotations

from web_router.constants_data_driven import (
    DATA_DRIVEN_LAST_RESORT,
    DATA_DRIVEN_ORDERS,
    DATA_DRIVEN_REASON,
    DATA_DRIVEN_REASON_BACKENDS,
    DATA_DRIVEN_REASON_LAST_RESORT,
    DATA_DRIVEN_REASON_NO_BACKEND,
    DATA_DRIVEN_REASON_OVERALL,
    DATA_DRIVEN_ROUTER_NAME,
    MSG_DATA_DRIVEN_BROWSE,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import Prefer, RouteDecision, RouteRequest, ToolName
from web_router.routing.data_driven.lines import search_attempts
from web_router.routing.data_driven.providers import ProviderTable, shipped_providers
from web_router.routing.data_driven.table import Row, ToolTable, shipped_table
from web_router.routing.route_tools import checked_prefer


class DataDrivenRouter:
    """``router="data-driven"``: asked by ``plan()`` and ``run()`` alone, named ``data-driven``."""

    route_tools: tuple[ToolName, ...] = ()
    route_modes = False
    router_name = DATA_DRIVEN_ROUTER_NAME

    def __init__(self, *, browse: bool = False, prefer: Prefer | None = None) -> None:
        """
        Args:
            browse: Whether ``browse`` may be ranked; without it, the row's next method is.
            prefer: How search's measured backends are ordered: ``cheap``, ``fast`` or ``accurate``. Unset, a
                request's own ``prefer`` is used, and with neither, the best measured quality goes first.

        Raises:
            ConfigurationError: for a ``browse`` that is not a bool, an unknown ``prefer``, or a shipped release
                this version cannot read.
        """
        if not isinstance(browse, bool):
            raise ConfigurationError(MSG_DATA_DRIVEN_BROWSE.format(got=browse))
        self.browse, self.prefer = browse, checked_prefer(prefer)
        self.table: ToolTable = shipped_table()
        self.providers: ProviderTable = shipped_providers()
        self.extra_modes = self.providers.extra_modes

    @property
    def version(self) -> str:
        """The trained release this router decides with, such as ``0.1.2``."""
        return self.table.version

    def route(self, req: RouteRequest) -> RouteDecision:
        """The methods of the prompt's row, most common in training first, then ``search``; and when the call
        searches, the measured search backends it was shown, in the preference's order."""
        row = self.table.row(req.prompt)
        tools = self._ranked(row, req)
        share = row.counts.get(tools[0], 0) / sum(row.counts.values())
        reason, prefer = self._reason(row, tools[0]), self.prefer or req.prefer
        runs = req.pinned_tool or next((t for t in tools if any(c.tool == t for c in req.candidates)), None)
        attempts = search_attempts(req, self.providers.search, prefer) if runs == "search" else None
        if runs == "search" and req.pinned_tool is None and tools[0] != runs:  # the first method has no backend
            reason = DATA_DRIVEN_REASON_NO_BACKEND.format(reason=reason, tool=tools[0])
        if attempts is None:
            return RouteDecision(tools=tools, confidence=share, reason=reason)
        order = DATA_DRIVEN_ORDERS.get(prefer, DATA_DRIVEN_ORDERS[None])
        reason = DATA_DRIVEN_REASON_BACKENDS.format(reason=reason, order=order, measured=self.providers.measured)
        return RouteDecision(tools=tools, attempts=attempts, confidence=share, reason=reason, prefer=prefer)

    def _ranked(self, row: Row, req: RouteRequest) -> list[ToolName]:
        held_back = {"browse": not self.browse, "extract": not req.urls}
        ranked = sorted((tool for tool, count in row.counts.items() if count), key=lambda tool: -row.counts[tool])
        tools = [tool for tool in ranked if not held_back.get(tool)]
        return tools if DATA_DRIVEN_LAST_RESORT in tools else [*tools, DATA_DRIVEN_LAST_RESORT]

    def _reason(self, row: Row, tool: ToolName) -> str:
        if not row.values:
            return DATA_DRIVEN_REASON_OVERALL.format(version=self.version, tool=tool)
        features = ", ".join(f"{name}={value}" for name, value in zip(self.table.features, row.values, strict=False))
        if not row.counts.get(tool):
            return DATA_DRIVEN_REASON_LAST_RESORT.format(version=self.version, features=features)
        return DATA_DRIVEN_REASON.format(version=self.version, tool=tool, features=features)
