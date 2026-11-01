"""You.com research and answer: their endpoints, effort mapping, status table and answer fields.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. The env var and the provider's
place in ``auto`` stay there, beside every other backend's.
"""

from __future__ import annotations

import re

from web_router.types import Recency, ResearchEffort, ResearchState

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

# Verified live on 2026-11-01. An answer is POST /v1/answer on api.you.com,
# the research host, answering in about two seconds with `answer` (markdown,
# citing as `[[1, 2]]`), `citations` -- only the sources the answer cites, in
# the order the markers count them from 1, each `{source, excerpts}` -- and
# `results.web`, every page considered, which carries the cited pages' titles.
# No request id. A query over 400 characters is a 422 naming the limit.

YOU_ANSWER_PATH = "/v1/answer"

YOU_ANSWER_PARAM_NAMES: dict[str, str] = {
    "include_domains": "include_domains",
    "exclude_domains": "exclude_domains",
    "country": "country",
}
"""Normalized parameter -> You.com field. Each was seen to apply live: an emergency-number question answered 999 for
GB and 911 for US.

``locale`` is left unmapped: ``language`` was accepted, but ``FR`` changed neither
the answer nor its sources, so nothing showed it doing what ``locale`` means.
"""

YOU_ANSWER_DOMAINS_EXCLUSIVE = frozenset({"include_domains", "exclude_domains"})
"""Answered 422 ``mutually_exclusive`` when sent together, so the pair is refused before the call."""

MSG_YOU_ANSWER_DOMAINS_EXCLUSIVE = (
    "You.com answers with one domain list, not both: send include_domains or exclude_domains."
)

YOU_ANSWER_FRESHNESS_FIELD = "freshness"

YOU_ANSWER_RECENCY: dict[Recency, str] = {
    "day": "day",
    "week": "week",
    "month": "month",
    "year": "year",
}
"""You.com's own values, one for one. Live, ``week`` kept every result to the last two weeks' pages."""

YOU_ANSWER_MARKER = re.compile(r"\[\[(\d+(?:,\s*\d+)*)\]\]")
"""A citation marker in the answer: ``[[1, 2]]``, numbering ``citations`` from 1."""
