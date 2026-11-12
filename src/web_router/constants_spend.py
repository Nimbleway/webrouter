"""The spend log's report keys and messages, kept apart so :mod:`web_router.constants` stops growing."""

from __future__ import annotations

SPEND_REPORT_KEYS = ("day", "provider", "method", "cost_source")
"""What :func:`~web_router.spend_report` can group by."""

MSG_SPEND_REPORT_KEY = "A spend report groups by {keys}; got {key!r}."
MSG_SPEND_SINK = "log_spend() takes a file path, an object with record(entry), or None; got a {kind}."
MSG_SPEND_SINK_FAILED = "The spend log %s could not record a call (%s: %s); the response is returned regardless."
