"""Rule 7 of the merge rule: extract with no URL to read searches instead, and says so.

Experimental. Apart from :mod:`.merge` so each stays small. It is read twice:
before the options are checked, for a router that gave no URL or gave URLs a
check refuses; and after, for one whose every URL a later check dropped -- over
the budget, say -- so an unpinned call never ends on extract with nothing to
fetch. A pinned extract stays extract: the caller chose it. So does one with
no search candidate to move to, its refused URLs noted: a search with no
backend could run nothing.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from web_router.constants_candidates import ONE_PAGE_URL
from web_router.constants_merge import (
    EXTRACT_TOOL,
    LAST_RESORT_TOOL,
    MSG_EXTRACT_NO_URL,
    NOTE_EXTRACT,
    NOTE_EXTRACT_URLS,
)
from web_router.constants_params import PRICED_QUERY
from web_router.routing.candidates import REQUEST_OF, base_params
from web_router.routing.contract import RouteDecision, RouteRequest, ToolName
from web_router.routing.merge_params import router_url_refusal
from web_router.routing.merge_urls import UrlCheck
from web_router.types import NormalizedRequest


def _unpinned_with_no_url(tool: str, request: RouteRequest) -> bool:
    """An unpinned extract with no prompt URL, and a search candidate to move to: one with none stays extract."""
    can_search = any(c.tool == LAST_RESORT_TOOL for c in request.candidates)
    return tool == EXTRACT_TOOL and request.pinned_tool is None and not request.urls and can_search


def extract_or_search(
    tool: str,
    request: RouteRequest,
    decision: RouteDecision,
    caller_set: Collection[str],
    url_check: UrlCheck | None,
    ignored: dict[str, str],
) -> str:
    """``tool``, unless it is an unpinned extract with no URL the router gave that passes every check."""
    refused = router_url_refusal(decision, url_check, caller_set)
    gave_urls = (decision.params or {}).get(EXTRACT_TOOL, {}).get("urls") is not None
    if refused is not None and tool == EXTRACT_TOOL and request.pinned_tool is None and not request.urls:
        ignored[NOTE_EXTRACT_URLS] = refused
    if not _unpinned_with_no_url(tool, request) or (gave_urls and not refused):
        return tool
    ignored[NOTE_EXTRACT] = MSG_EXTRACT_NO_URL
    return LAST_RESORT_TOOL


def lost_its_urls(tool: str, request: RouteRequest, params: Mapping[str, Any], ignored: dict[str, str]) -> bool:
    """Whether ``tool`` is an unpinned extract whose router URLs the option checks all dropped; noted if so."""
    if not _unpinned_with_no_url(tool, request) or params.get("urls"):
        return False
    ignored[NOTE_EXTRACT] = MSG_EXTRACT_NO_URL
    return True


def prompt_request(tool: ToolName, request: RouteRequest) -> NormalizedRequest:
    """The request ``tool`` runs ``request``'s prompt with, extract on one page when the prompt names no URL."""
    prompt = request.prompt or PRICED_QUERY  # priced by backend and options, never by the prompt
    return REQUEST_OF[tool](**base_params(tool, prompt, request.urls or (ONE_PAGE_URL,)))
