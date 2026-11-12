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
from typing import Any, TypeVar

from pydantic import BaseModel

from web_router.constants_cost import COST_ESTIMATED
from web_router.routing.attempts import Attempt
from web_router.routing.contract import ToolName
from web_router.routing.pricing import attempt_cost
from web_router.types import NormalizedRequest, provider_name

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


def metered(response: _ResponseT, tool: ToolName, request: NormalizedRequest, started: float) -> _ResponseT:
    """``response`` with the seconds since ``started`` (a :func:`time.monotonic` reading) and its cost."""
    update: dict[str, Any] = {"elapsed_s": time.monotonic() - started}
    if getattr(response, "cost_usd", None) is None:
        cost = estimated_cost(tool, getattr(response, "provider", None), request)
        if cost is not None:
            update.update(cost_usd=cost, cost_source=COST_ESTIMATED)
    return response.model_copy(update=update)


def estimated_cost(tool: ToolName, provider: str | None, request: NormalizedRequest) -> float | None:
    """What ``provider`` is estimated to charge for ``request``, by the rule a budget charges by; ``None`` unknown."""
    if not provider:
        return None
    return attempt_cost(Attempt(tool=tool, provider=provider_name(provider), request=request))
