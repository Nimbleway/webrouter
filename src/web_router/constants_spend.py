"""The spend log's report keys and messages, kept apart so :mod:`web_router.constants` stops growing."""

from __future__ import annotations

from typing import Final

SPEND_REPORT_KEYS = ("day", "provider", "method", "cost_source", "outcome")
"""What :func:`~web_router.spend_report` can group by."""

MSG_SPEND_REPORT_KEY = "A spend report groups by {keys}; got {key!r}."
MSG_SPEND_SINK = "log_spend() takes a file path, an object with record(entry), or None; got a {kind}."
MSG_SPEND_LINE_SKIPPED = "Skipped a spend log line that is not a whole entry: %.80s"
MSG_SPEND_SINK_FAILED = "The spend log %s could not record a call (%s: %s); the response is returned regardless."

OUTCOME_ANSWERED: Final = "answered"
OUTCOME_FAILED: Final = "failed"
OUTCOME_EMPTY: Final = "empty"
"""What a spend log line's attempt came to: see :data:`~web_router.spend.SpendOutcome`."""
