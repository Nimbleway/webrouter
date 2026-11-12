"""Messages for the typed errors, kept apart from :mod:`web_router.constants`, so that file stops growing."""

from __future__ import annotations

MSG_INVALID_PARAMETER = "Invalid {field}: {reason}"
"""One request option the request model refused, in one line, without a link to the validator's docs."""

MSG_REUSED_LOOP = (
    ". The router's async clients belong to the event loop that first used them: build one AsyncWebRouter per loop "
    "(per asyncio.run), or close it with aclose() before that loop ends."
)
"""Added to a backend failure that came from reusing an async router on a second event loop."""

MSG_KEY_REFUSED = "%s refused its credential (HTTP %s); auto moved on to the next backend. Check that key."
"""Logged at WARNING, by provider and status, when a configured backend answers 401 or 403."""
