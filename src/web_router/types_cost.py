"""What a call cost and how long it took: the fields every routable response carries.

Search, extract, answer, research and browse responses declare them, apart
from :mod:`web_router.types` so that file stops growing; this imports
nothing of ours. Each field is an ``Annotated`` alias, so the five responses
declare it with one line and one description.

``cost_usd`` is a fact where the backend states it -- Perplexity's
``usage.cost``, Exa's ``costDollars`` -- and an estimate everywhere else, from
the price list :mod:`web_router.prices` ships or, for a backend billed by
tokens, the mean a published benchmark measured. ``cost_source`` says which,
so an estimate is never read as a bill.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

CostSource = Literal["reported", "estimated"]
"""Where :attr:`cost_usd` came from: the backend's own figure, or an estimate made here."""

ElapsedSeconds = Annotated[
    float | None,
    Field(
        description=(
            "Wall-clock seconds from the call to its answer, every backend `auto` tried before it included. "
            "None on a response a provider built outside a router's call."
        )
    ),
]

CostUsd = Annotated[
    float | None,
    Field(
        description=(
            "What the backend that answered charged for it, in US dollars: its own figure where its response "
            "states one, else an estimate (`cost_source` says which). None when neither is known. Attempts that "
            "failed before it are in `decision`."
        )
    ),
]

CostSourceField = Annotated[
    CostSource | None,
    Field(
        description=(
            "`reported` when the backend stated `cost_usd`; `estimated` when it is the list price, or for a "
            "backend billed by tokens the mean a published benchmark measured. None when there is no cost."
        )
    ),
]
