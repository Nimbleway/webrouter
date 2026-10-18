"""You.com research: its endpoints, effort mapping and status table.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. The env var and the provider's
place in ``auto`` stay there, beside every other backend's.
"""

from __future__ import annotations

from web_router.types import ResearchEffort, ResearchState

# Verified live on 2026-10-16. Research lives on api.you.com, not on the
# ydc-index.io host search uses: there the same path answers 403. POST with
# `background` returns a task `queued` under `task_id`; GET reports it under
# `id` through `running` to `completed`, with the answer in `result.output`.
# No cancel is offered: POST .../cancel is a 404 and DELETE a 405. The
# `failed` and `cancelled` statuses, and a failed task's `error`, are from
# documentation: no live run reached either.

YOU_RESEARCH_BASE_URL = "https://api.you.com"
YOU_RESEARCH_PATH = "/v1/research"
YOU_RESEARCH_STATUS_PATH = "/v1/research/{job_id}"

YOU_RESEARCH_BACKGROUND = True
"""Always sent: without it the request blocks for the whole run, which a research job must not."""

YOU_RESEARCH_EFFORTS: dict[ResearchEffort, str] = {
    # `research_effort` runs lite, standard, deep and exhaustive against the
    # four normalized levels one for one, and defaults to standard, so an unset
    # effort sends nothing. `frontier` sits above exhaustive at well over twice
    # its price and runs for up to hours, so `max` stops at exhaustive; a caller
    # who wants frontier asks for it through `provider_options`.
    "low": "lite",
    "medium": "standard",
    "high": "deep",
    "max": "exhaustive",
}

YOU_RESEARCH_STATES: dict[str, ResearchState] = {
    "queued": "queued",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}
