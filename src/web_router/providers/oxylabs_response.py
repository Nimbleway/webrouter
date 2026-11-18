"""An Oxylabs Web API scrape's answer as a live data response: each result's parsed fields are its records.

A run asks for ``output: ["json"]``, so each result carries the scraper's
fields under ``json`` -- one object, or a list of them -- and its fetch under
``metadata``. A page that was not fetched, or that came back with no fields,
raises: a run that brings nothing back is not a success.
"""

from __future__ import annotations

from typing import Any

from web_router.constants_live_data_vendors import (
    MSG_OXYLABS_NO_FIELDS,
    MSG_OXYLABS_NOT_FETCHED,
    OXYLABS_FAILED_FETCH_FLOOR,
)
from web_router.exceptions import ProviderError
from web_router.types_live_data import LiveDataRequest, LiveDataResponse


def _records(provider: str, request: LiveDataRequest, result: dict[str, Any]) -> list[dict[str, Any]]:
    """One result's parsed fields; raises for a page not fetched or with none."""
    fields, metadata = result.get("json"), result.get("metadata") or {}
    status, url = metadata.get("status_code"), metadata.get("url")
    if isinstance(status, int) and status >= OXYLABS_FAILED_FETCH_FLOOR:
        raise ProviderError(provider, MSG_OXYLABS_NOT_FETCHED.format(source=request.source, status=status, url=url))
    rows = [fields] if isinstance(fields, dict) else fields if isinstance(fields, list) else []
    parsed = [row for row in rows if isinstance(row, dict) and row]
    if not parsed:
        raise ProviderError(provider, MSG_OXYLABS_NO_FIELDS.format(source=request.source, url=url))
    return parsed


def to_response(provider: str, request: LiveDataRequest, payload: Any) -> LiveDataResponse:
    """Every result's records; ``metadata.timestamp`` stays in ``extra``, as it is not documented as the fetch time."""
    body = payload if isinstance(payload, dict) else {}
    results = [row for row in body.get("results") or [] if isinstance(row, dict)]
    if not results:
        raise ProviderError(provider, MSG_OXYLABS_NO_FIELDS.format(source=request.source, url=None))
    records = [record for result in results for record in _records(provider, request, result)]
    first, meta = results[0].get("metadata") or {}, body.get("metadata") or {}
    return LiveDataResponse(
        provider=provider,
        source=request.source,
        inputs=dict(request.inputs),
        records=records,
        url=first.get("url"),
        status_code=first.get("status_code"),
        request_id=str(meta["request_id"]) if meta.get("request_id") is not None else None,
        extra={"metadata": meta, "results": [row.get("metadata") or {} for row in results]},
        raw=payload,
    )
