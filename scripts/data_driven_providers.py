"""The data-driven router's provider table, made from a delivered lookup table: ``LOOKUP=<file>``.

The lookup table holds, per method, every backend tier a benchmark measured: its quality with a confidence
interval, its price and its median latency. A row ships only when web-router can make the call it was measured
with: ``search(query, max_results=10, search_depth=<the row's>, effort=<the row's>)``.

- A row whose ``call`` names a candidate ``id`` is kept only when that candidate sends the same request body as
  the measured call: the id is the delivery's claim, and the body is the check.
- A row whose ``call`` has ``"id": null`` ran outside web-router, on a backend it does not have, and is left out.
- A row with no ``id`` is matched by trying the backend's default, its depth and each of its tiers at that depth.

Every row left out is listed with why.
"""

from __future__ import annotations

from typing import Any

from web_router.exceptions import ProviderNotInstalledError, WebRouterError
from web_router.providers._pinning import pinned_request, split_pin
from web_router.registry import available_providers, get_provider_class
from web_router.routing.data_driven.providers import MEASURES
from web_router.routing.entry_names import candidate_id
from web_router.types import SearchRequest

MEASURED = {"query": "q", "max_results": 10}
"""What every measured search shared, besides its depth and effort."""

MEASURED_DEPTH = "fast"
"""The depth a row was measured at unless its ``call`` names another."""

SINGLE_TIER = "base"
"""The lookup table's effort for a backend measured at one tier: its call sets no effort."""

WHY_NO_BACKEND = "web-router has no search backend of this name"
WHY_NO_MODE = "no mode of this backend sends the request the row was measured with"
WHY_ID_DIFFERS = "the candidate {id} does not send the request the row was measured with"
WHY_ID_NOT_SHOWN = "the id {id} is not one a router is shown: it is not the candidate's own spelling"


def _wanted(provider: Any, depth: str, effort: str | None) -> Any:
    return provider._build_payload(SearchRequest(**MEASURED, search_depth=depth, effort=effort))


def _sent(provider: Any, mode: str | None) -> Any:
    """What ``provider`` sends at ``mode``, its default for none; ``None`` when it refuses the mode."""
    try:
        request = SearchRequest(**MEASURED)
        return provider._build_payload(pinned_request(provider, request, mode) if mode else request)
    except WebRouterError:
        return None


def measured_mode(provider: Any, effort: str | None, depth: str = MEASURED_DEPTH) -> tuple[bool, str | None]:
    """Whether a mode of ``provider`` sends what the measured call sent, and which: ``None`` for its default."""
    want = _wanted(provider, depth, effort)
    for mode in (None, depth, *(f"{tier}+{depth}" for tier in getattr(provider, "tiers", ()))):
        if _sent(provider, mode) == want:  # the default first: a mode that sends it is never shown beside it
            return True, mode if mode is None else str(pinned_request(provider, SearchRequest(**MEASURED), mode).mode)
    return False, None


def _canonical(provider: Any, mode: str) -> str | None:
    """The mode a call is shown ``mode`` under: none when it sends what the backend's default sends."""
    if not mode or _sent(provider, mode) == _sent(provider, None):
        return None
    return str(pinned_request(provider, SearchRequest(**MEASURED), mode).mode)


def claimed_id(provider: Any, name: str, claimed: str, effort: str | None, depth: str) -> str | None:
    """Why the candidate the delivery names for a row cannot ship; ``None`` when it can.

    It has to be ``name``'s, send the measured request, and be spelled as the id a call is shown: a router's
    attempt naming any other spelling is not a candidate, so the row would never be tried.
    """
    backend, mode = split_pin(claimed)
    if backend != name or _sent(provider, mode or None) != _wanted(provider, depth, effort):
        return WHY_ID_DIFFERS.format(id=claimed)
    if claimed != candidate_id("search", name, _canonical(provider, mode)):
        return WHY_ID_NOT_SHOWN.format(id=claimed)
    return None


def _placed(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """A lookup row's candidate id, or why it has none."""
    call = row.get("call") or {}
    name, effort = row["provider"], None if row["effort"] == SINGLE_TIER else row["effort"]
    if ("id" in call and call["id"] is None) or name not in available_providers():
        return None, WHY_NO_BACKEND
    depth = call.get("search_depth", MEASURED_DEPTH)
    try:
        provider = get_provider_class(name)(api_key="unused")
        if "id" in call:
            why = claimed_id(provider, name, call["id"], effort, depth)
            return (call["id"], None) if why is None else (None, why)
        found, mode = measured_mode(provider, effort, depth)
    except ProviderNotInstalledError:
        raise  # a backend this environment cannot build says nothing about the row: install every extra
    except (WebRouterError, TypeError) as exc:
        return None, f"{WHY_NO_MODE} ({type(exc).__name__})"
    return (candidate_id("search", name, mode), None) if found else (None, WHY_NO_MODE)


def provider_table(lookup: dict[str, Any]) -> dict[str, Any]:
    """The shipped table: each search row web-router can run as measured, by candidate id, and the rest with why."""
    kept, left_out = [], []
    for row in lookup["tools"]["search"]["all"]:
        cid, why = _placed(row)
        if cid is None:
            left_out.append({"arm": row["arm"], "why": why})
        else:
            kept.append({"id": cid, "arm": row["arm"], **{field: row.get(field) for field in MEASURES}})
    about = "Written by `make data-driven-router`; do not edit by hand."
    head = {"_about": about, "source": lookup["source"], "judge": lookup["judge"], "measured": lookup["version"]}
    return {**head, "search": kept, "left_out": left_out}
