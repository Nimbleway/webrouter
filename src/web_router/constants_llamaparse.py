"""LlamaParse: its endpoints, the tier it is asked for, and how long a parse is waited on.

Kept apart from :mod:`web_router.constants`, as the other newer backends' are,
so that file stops growing. The env var and provider name stay there, beside
every other backend's.
"""

from __future__ import annotations

from web_router.types import ExtractFormat

# LlamaCloud's Parse API v2 (developers.llamaindex.ai, API v2 guide): a job is created from a URL or an upload and
# polled until it ends; `expand` asks the poll for the result. Read from the reference and the llama-cloud SDK's types,
# not a live call: no account was available.
LLAMAPARSE_BASE_URL = "https://api.cloud.llamaindex.ai"
LLAMAPARSE_PARSE_PATH = "/api/v2/parse"
LLAMAPARSE_UPLOAD_PATH = "/api/v2/parse/upload"
LLAMAPARSE_JOB_PATH = "/api/v2/parse/{job_id}"
LLAMAPARSE_API_KEY_PREFIX = "Bearer "

LLAMAPARSE_TIER = "cost_effective"
"""Sent on every job, since the API requires one: its balanced tier, 3 credits a page.

``fast`` is rule-based, which LiteParse already does locally for free. ``provider_options={"tier": "agentic"}``
overrides it, as it does any field.
"""

LLAMAPARSE_VERSION = "latest"

LLAMAPARSE_EXTRACT_FORMATS: dict[ExtractFormat, str] = {"markdown": "markdown_full", "text": "text_full"}
"""Our format to the ``expand`` value that returns it, which is also the response field holding it."""

LLAMAPARSE_EXTRACT_DEFAULT_FORMAT: ExtractFormat = "markdown"

LLAMAPARSE_DONE = "COMPLETED"
LLAMAPARSE_ENDED = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
LLAMAPARSE_POLL_SECONDS = 1.0
LLAMAPARSE_WAIT_SECONDS = 300.0
"""How long one document's parse is waited on when the call sets no ``timeout``. The job bills either way."""

MSG_LLAMAPARSE_FAILED = "LlamaParse job {job_id} ended {status}: {error}"
MSG_LLAMAPARSE_STILL_RUNNING = "LlamaParse job {job_id} was still {status} when the wait ran out; it bills either way"
MSG_LLAMAPARSE_NO_JOB = "LlamaParse answered without a job id (fields: {fields})"
MSG_LLAMAPARSE_JOB_ERROR = "LlamaParse job {job_id} was accepted (it bills either way), then: {error}"
