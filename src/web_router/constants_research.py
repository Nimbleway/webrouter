"""Messages shared by the research backends, kept apart from :mod:`web_router.constants` so that file stops growing."""

from __future__ import annotations

MSG_RESEARCH_RESULT_UNFETCHED = "the run finished, but its answer could not be fetched yet: {error}"
"""The detail of a run started and finished in one call whose answer could not be read: it is reported running."""
