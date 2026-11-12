"""The error a per-call budget raises.

Listed in the tree of :mod:`web_router.exceptions`, which does not import it.
"""

from __future__ import annotations

from collections.abc import Mapping

from web_router.constants_budget import MSG_BUDGET_EXCEEDED, SKIPPED
from web_router.exceptions import WebRouterError


class BudgetExceededError(WebRouterError):
    """Nothing the call could run fits its :class:`~web_router.Budget`.

    Raised before anything starts, naming each candidate the budget skipped
    and why, as :attr:`skipped` holds them. Not a
    :class:`~web_router.ProviderError`, as
    :class:`~web_router.DeadlineExceededError` is not: ``auto`` never takes it
    as a reason to try the next member. A time cap reached mid-call raises the
    method's own timeout error instead.
    """

    def __init__(self, skipped: Mapping[str, str]) -> None:
        self.skipped = dict(skipped)
        detail = "; ".join(SKIPPED.format(id=name, reason=reason) for name, reason in self.skipped.items())
        super().__init__(MSG_BUDGET_EXCEEDED.format(skipped=detail))
