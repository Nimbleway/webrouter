"""What a vendor mode's failure means for the backend's other modes: refused, or the backend stopped.

Experimental. When a router names attempts (``RouteDecision.attempts``), one
backend may run in several modes in a call. A failure in one of them is read
from what :class:`~web_router.exceptions.ProviderError` already says, its
``kind`` and ``status_code``, and has one of three outcomes:

1. :func:`mode_refused`: the backend refused this mode's request, so its charge
   is taken back and the backend's next mode runs.
2. ``kind`` ``local`` or ``target``: no backend could do better, so the call
   raises, as it always has.
3. Anything else stops the backend for the call: its later attempts are passed
   over, each recorded as :data:`BACKEND_FAILED`. The one exception is a
   timeout whose next mode's p90 fits the time left
   (:mod:`web_router.providers._mode_timeout`).

The rule is narrow on purpose: it reads the status the error carries and walks
no cause chain, so a status it cannot read stops the backend.
"""

from __future__ import annotations

from web_router.types_errors import ERROR_KIND_BAD_REQUEST

REFUSED_STATUSES = frozenset({400, 422})
"""The statuses by which a backend says it will not take this request: the mode's, not the backend's, fault."""

OUT_OF_CREDITS_MARKERS = ("insufficient_funds", "insufficient_quota", "exceeded your current quota")
"""Phrases, lowercased, by which a backend says the account ran out of credit, under whatever status it answers:
never a refused mode, since every other mode would fail the same way."""

BACKEND_FAILED = "backend_failed"
"""The hop recorded for an attempt passed over because an earlier attempt of its backend failed in the call."""


def mode_refused(exc: BaseException) -> bool:
    """Whether ``exc`` is a backend refusing one mode's request: ``bad_request`` with 400 or 422, and no credit
    ran out. Any other failure, an unknown status included, is not."""
    if (
        getattr(exc, "kind", None) != ERROR_KIND_BAD_REQUEST
        or getattr(exc, "status_code", None) not in REFUSED_STATUSES
    ):
        return False
    text = str(exc).lower()
    return not any(marker in text for marker in OUT_OF_CREDITS_MARKERS)
