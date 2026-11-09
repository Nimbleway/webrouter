"""Where a shared budget stands: what :meth:`~web_router.BudgetScope.status` returns. Experimental."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class BudgetStatus:
    """Where a shared budget stands. Experimental."""

    spent_usd: float
    """The estimates settled by calls that have ended, failed attempts included."""

    reserved_usd: float
    """The estimates reserved by calls still running."""

    remaining_usd: float | None
    """The least, over this pool and the ones in force outside it, of each cap less what is spent and reserved.

    ``None`` with no cost cap.
    """

    elapsed_s: float
    """Seconds since the budget was made."""

    time_left_s: float | None
    """Seconds left on the budget's clock, never below zero; ``None`` with no time limit."""
