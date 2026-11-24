"""The built-in ``data-driven`` router: a request's method, from a table trained on labelled requests.

Experimental. It needs no key and calls nothing: five regular expressions read the prompt
(:mod:`.features`), and the shipped table (:mod:`.table`) says which method the training requests with those
values were served by. It ranks the methods of that row, most common first, and ranks no backends: they run
in ``auto``'s order. It is the routers' default, attached unless ``router=`` says otherwise, and it declares
``route_tools=()``, so only
:meth:`~web_router.WebRouter.plan` and :meth:`~web_router.WebRouter.run` ask it.

Two methods are held back. ``browse`` is ranked only by a router built with ``browse=True``: a browser agent
is slow and billed, so a caller opts in. ``extract`` is ranked only when the call names a URL it may read.
``answer`` is not a method the table was trained on, so it is never picked.
"""

from __future__ import annotations

from web_router.constants_data_driven import (
    DATA_DRIVEN_LAST_RESORT,
    DATA_DRIVEN_REASON,
    DATA_DRIVEN_REASON_LAST_RESORT,
    DATA_DRIVEN_REASON_OVERALL,
    DATA_DRIVEN_ROUTER_NAME,
    MSG_DATA_DRIVEN_BROWSE,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.contract import RouteDecision, RouteRequest, ToolName
from web_router.routing.data_driven.table import Row, ToolTable, shipped_table


class DataDrivenRouter:
    """``router="data-driven"``: asked by ``plan()`` and ``run()`` alone, named ``data-driven``."""

    route_tools: tuple[ToolName, ...] = ()
    route_modes = False
    router_name = DATA_DRIVEN_ROUTER_NAME

    def __init__(self, *, browse: bool = False) -> None:
        if not isinstance(browse, bool):
            raise ConfigurationError(MSG_DATA_DRIVEN_BROWSE.format(got=browse))
        self.browse = browse
        self.table: ToolTable = shipped_table()

    @property
    def version(self) -> str:
        """The trained release this router decides with, such as ``0.1.1``."""
        return self.table.version

    def route(self, req: RouteRequest) -> RouteDecision:
        """The methods of the prompt's row, most common in training first, then ``search``."""
        row = self.table.row(req.prompt)
        tools = self._ranked(row, req)
        share = row.counts.get(tools[0], 0) / sum(row.counts.values())
        return RouteDecision(tools=tools, confidence=share, reason=self._reason(row, tools[0]))

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
