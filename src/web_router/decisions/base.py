"""The decider slot: what any decision provider implements.

:class:`DecisionProvider` is exactly the shape :meth:`~web_router.WebRouter.classify`
already calls, so :class:`~web_router.decisions.JevDecider` fits as it is, and
so does a vendor's own class: ``decide(state, questions)`` answers each of
ROUTE_QUESTIONS with an :class:`~web_router.decisions.Answer`, and ``close()``
releases whatever the provider holds.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from web_router.decisions.route import Answer


@runtime_checkable
class DecisionProvider(Protocol):
    """Anything that can answer ROUTE_QUESTIONS about a query.

    When ``decide`` cannot answer -- it raises anything --
    :meth:`~web_router.WebRouter.classify` takes the default route and records
    ``decider_error``. An optional attribute describes the provider:
    ``version``, naming the model or rules behind the answers.
    """

    name: str

    def decide(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Answer]: ...

    def close(self) -> None: ...
