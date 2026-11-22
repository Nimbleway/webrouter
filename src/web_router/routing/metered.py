"""What a call cost and how long it took, stamped on its response: ``elapsed_s``, ``cost_usd``, ``cost_source``.

Every routable method -- search, extract, answer, research and browse -- runs
through the routers' ``_routed`` / ``_arouted``, budget or not, and its
response leaves through :func:`metered`:

- ``elapsed_s`` is the wall-clock time of the whole call, from the router's
  method to its answer: a research or browse wait, and every member ``auto``
  tried and moved past, included.
- ``cost_usd`` is the answering backend's own stated cost when its provider
  read one (``cost_source="reported"``); otherwise the estimate
  :func:`~.pricing.attempt_cost` makes for that backend and the request it was
  sent -- the same rule a budget charges by (``cost_source="estimated"``);
  otherwise ``None``. Attempts that failed before the answer are not in it:
  ``decision`` holds them under a budget or a router.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from web_router.constants_cost import COST_ESTIMATED, COST_REPORTED, COST_STATED
from web_router.routing.attempts import Attempt
from web_router.routing.contract import ToolName
from web_router.routing.pricing import attempt_cost
from web_router.types import NormalizedRequest, provider_name

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


def metered(response: _ResponseT, tool: ToolName, request: NormalizedRequest, started: float) -> _ResponseT:
    """``response`` with the seconds since ``started`` (a :func:`time.monotonic` reading) and its cost."""
    update: dict[str, Any] = {"elapsed_s": time.monotonic() - started}
    if getattr(response, "cost_usd", None) is None:
        cost = _part_cost(tool, response, request)
        if cost is not None:
            update.update(cost_usd=cost, cost_source=COST_ESTIMATED)
    return response.model_copy(update=update)


def estimated_cost(tool: ToolName, provider: str | None, request: NormalizedRequest) -> float | None:
    """What ``provider`` is estimated to charge for ``request``, by the rule a budget charges by; ``None`` unknown."""
    if not provider:
        return None
    return attempt_cost(Attempt(tool=tool, provider=provider_name(provider), request=request))


def combined_cost(tool: ToolName, parts: Sequence[tuple[BaseModel, NormalizedRequest]]) -> dict[str, Any]:
    """The cost fields of one answer several backends' calls made together: each call's cost, summed.

    A part's cost is its own ``cost_usd`` when set, else the estimate for its
    backend and request. Its source is :func:`_summed_source`'s.
    When any part's cost is unknown so is the sum: both fields are set to
    ``None``, which :func:`metered` leaves as they are rather than estimating
    the first backend's share alone.
    """
    costs = [_part_cost(tool, response, request) for response, request in parts]
    if any(cost is None for cost in costs):
        return {"cost_usd": None, "cost_source": None}
    return {
        "cost_usd": sum(cost for cost in costs if cost is not None),
        "cost_source": _summed_source([response for response, _ in parts]),
    }


def _part_cost(tool: ToolName, response: BaseModel, request: NormalizedRequest) -> float | None:
    """``response``'s own cost, else its backend's estimate; ``None`` when a merge set it unknown on purpose."""
    cost = getattr(response, "cost_usd", None)
    if cost is not None or "cost_usd" in response.model_fields_set:  # a part unpriced in combined_cost
        return cost
    return estimated_cost(tool, getattr(response, "provider", None), request)


def collected_cost(responses: Sequence[BaseModel]) -> dict[str, Any]:
    """The cost fields of one answer built from several of a backend's answers: theirs summed, when each states one.

    A part that states nothing leaves the whole to :func:`metered`'s estimate,
    as before; a part left unknown on purpose (:func:`combined_cost`) leaves
    the whole unknown.
    """
    if not responses or any("cost_usd" not in response.model_fields_set for response in responses):
        return {}
    costs = [getattr(response, "cost_usd", None) for response in responses]
    if any(cost is None for cost in costs):
        return {"cost_usd": None, "cost_source": None}
    return {
        "cost_usd": sum(cost for cost in costs if cost is not None),
        "cost_source": _summed_source(responses),
    }


def _summed_source(parts: Sequence[BaseModel]) -> str:
    """The source of a sum of ``parts``' costs: theirs when they share one, ``reported`` when each states its own
    (a keyless part's $0 among them), else ``estimated``."""
    sources = {getattr(part, "cost_source", None) for part in parts}
    if len(sources) == 1:
        return str(sources.pop() or COST_ESTIMATED)
    return COST_REPORTED if sources <= COST_STATED else COST_ESTIMATED
