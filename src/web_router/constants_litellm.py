"""Search through a LiteLLM proxy: its Search API's path, auth and response fields.

LiteLLM's proxy (v1.79.0+) serves ``POST /v1/search/{search_tool_name}`` for the search tools its operator lists
under ``search_tools`` in ``config.yaml``, each naming a ``search_provider`` (``exa_ai``, ``tavily``,
``parallel_ai``...). The vendor key stays on the proxy; the caller sends its LiteLLM key. Documented at
docs.litellm.ai/docs/search (checked 2026-11-12). The proxy forwards the vendor's own request fields as they are,
so the vendor's mapping is reused whole; it answers in one shape for every vendor.
"""

from __future__ import annotations

from web_router.types import Provider

PROVIDER_EXA_LITELLM = Provider.EXA_LITELLM.value
PROVIDER_TAVILY_LITELLM = Provider.TAVILY_LITELLM.value
PROVIDER_PARALLEL_LITELLM = Provider.PARALLEL_LITELLM.value

ENV_LITELLM_PROXY_API_KEY = "LITELLM_PROXY_API_KEY"
ENV_LITELLM_PROXY_API_BASE = "LITELLM_PROXY_API_BASE"
"""The names LiteLLM's own SDK reads for its proxy, so a caller already set up for it needs nothing new."""

LITELLM_API_KEY_HEADER = "Authorization"
LITELLM_API_KEY_PREFIX = "Bearer "
LITELLM_SEARCH_PATH = "/v1/search/{tool}"
MSG_LITELLM_PROXY_URL = (
    "{provider!r} needs LITELLM_PROXY_API_BASE (or base_url) to be an https URL, or plain http to localhost,"
    " 127.0.0.0/8 or ::1, with no credentials, query or fragment: the proxy key rides on every request."
)

LITELLM_SEARCH_TOOL_BY_PROVIDER: dict[str, str] = {
    PROVIDER_EXA_LITELLM: "exa",
    PROVIDER_TAVILY_LITELLM: "tavily",
    PROVIDER_PARALLEL_LITELLM: "parallel",
}
"""The ``search_tool_name`` each provider calls unless ``search_tool`` names another: the operator picks these names,
and the vendor's own is the obvious one."""

LITELLM_ENV_BY_PROVIDER: dict[str, str | None] = dict.fromkeys(
    LITELLM_SEARCH_TOOL_BY_PROVIDER, ENV_LITELLM_PROXY_API_KEY
)
"""Each provider's credential, for ``ENV_BY_PROVIDER``: the proxy's key, never the vendor's."""

LITELLM_QUERY_FIELD = "query"
LITELLM_MAX_RESULTS_FIELD = "max_results"
"""LiteLLM's own request fields, sent beside the vendor's: Parallel's body has no ``query`` of its own."""

LITELLM_RESULTS_FIELD = "results"
LITELLM_SNIPPET_FIELD = "snippet"
LITELLM_EXCERPTS_FIELD = "excerpts"
LITELLM_DATE_FIELD = "date"
LITELLM_RAW_CONTENT_FIELD = "raw_content"
LITELLM_SCORE_FIELD = "score"
LITELLM_MAPPED_RESULT_FIELDS = frozenset(
    {
        "url",
        "title",
        LITELLM_SNIPPET_FIELD,
        LITELLM_EXCERPTS_FIELD,
        LITELLM_DATE_FIELD,
        LITELLM_RAW_CONTENT_FIELD,
        LITELLM_SCORE_FIELD,
    }
)
"""``raw_content`` (Tavily's page, when asked for) and ``score`` (Exa's and Tavily's) come only when the vendor
returns them, per docs.litellm.ai/docs/search/tavily and /exa_ai."""

LITELLM_METADATA_FIELD = "provider_metadata"
LITELLM_ANSWER_FIELD = "answer"
LITELLM_REQUEST_ID_FIELDS = ("request_id", "requestId")
"""The vendor's top-level fields other than its results -- Tavily's ``answer`` and ``request_id``, Exa's
``requestId`` -- which LiteLLM keeps under ``provider_metadata``."""
