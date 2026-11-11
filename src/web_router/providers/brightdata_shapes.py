"""Bright Data's answers, shaped as live data: its scrapers as sources, a scrape's rows as records.

Kept apart from :mod:`web_router.providers.brightdata_live_data`, which is
left with the requests. Each function takes the provider's name, so nothing
here needs a client.
"""

from __future__ import annotations

from typing import Any

from web_router.constants_live_data_vendors import (
    BRIGHTDATA_PENDING_STATUS,
    MSG_BRIGHTDATA_ALL_FAILED,
    MSG_BRIGHTDATA_PENDING,
)
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.providers.brightdata_names import Scraper
from web_router.types_live_data import LiveDataRequest, LiveDataResponse, LiveDataSample, LiveDataSource

_LISTED_MAPPED = frozenset({"id", "name", "description", "domain", "category"})


def to_response(provider: str, request: LiveDataRequest, status_code: int, payload: Any) -> LiveDataResponse:
    """A scrape's rows as records; a failed input's row goes to ``extra["errors"]``, never counted as a record."""
    if status_code == BRIGHTDATA_PENDING_STATUS:
        snapshot_id = (payload or {}).get("snapshot_id")
        raise ProviderError(provider, MSG_BRIGHTDATA_PENDING.format(source=request.source, snapshot_id=snapshot_id))
    rows = [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
    errors = [row for row in rows if row.get("error") or row.get("error_code")]
    records = [row for row in rows if row not in errors]
    if errors and not records:
        detail = "; ".join(str(row.get("error_code") or row.get("error")) for row in errors)
        raise ProviderError(provider, MSG_BRIGHTDATA_ALL_FAILED.format(source=request.source, errors=detail))
    return LiveDataResponse(
        provider=provider,
        source=request.source,
        inputs=dict(request.inputs),
        records=records,
        extra={"errors": errors} if errors else {},
        raw=payload,
    )


def listed(provider: str, scraper: Scraper) -> LiveDataSource:
    """One scraper and collection method as a catalogue entry."""
    item = scraper.item
    how = "by URL" if not scraper.discovers else "discovered by " + scraper.discover_by.replace("_", " ")
    title = str(item.get("name") or scraper.dataset_id)
    return LiveDataSource(
        provider=provider,
        name=scraper.name,
        description=" -- ".join(part for part in (f"{title}, {how}", item.get("description")) if part),
        domain=item.get("domain"),
        entity_type=item.get("category"),
        published=True,
        extra={
            "dataset_id": scraper.dataset_id,
            "collection_method": scraper.method,
            **{key: value for key, value in item.items() if key not in _LISTED_MAPPED},
        },
    )


def described(provider: str, scraper: Scraper, payload: Any) -> LiveDataSource:
    """The full description of ``scraper``'s method, from the management API's entry for its dataset."""
    items = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
    item = next((entry for entry in items if entry.get("id") == scraper.dataset_id), None)
    if item is None:
        raise LiveDataSourceNotFoundError(provider, scraper.name)
    found = item.get("scrapers")
    methods: dict[str, Any] = found if isinstance(found, dict) else {}
    method: dict[str, Any] = methods.get(scraper.method) or {}
    entry = listed(
        provider, Scraper(name=scraper.name, dataset_id=scraper.dataset_id, method=scraper.method, item=item)
    )
    return entry.model_copy(
        update={
            "input_schema": _fields_schema(method.get("input_schema")),
            "output_schema": _fields_schema(method.get("output_fields")),
            "samples": _samples(method.get("sample_input"), method.get("sample_output")),
            "extra": {**entry.extra, "collection_methods": sorted(methods)},
            "raw": item,
        }
    )


def _fields_schema(fields: Any) -> dict[str, Any] | None:
    """A JSON Schema naming the fields Bright Data lists, with no types: the API states none."""
    if isinstance(fields, dict):
        names = list(fields)
    elif isinstance(fields, list):
        names = [field if isinstance(field, str) else (field or {}).get("name") for field in fields]
    else:
        return None
    names = [name for name in names if isinstance(name, str)]
    return {"type": "object", "properties": {name: {} for name in names}} if names else None


def _samples(inputs: Any, outputs: Any) -> list[LiveDataSample]:
    """Bright Data's samples: an input object or, as its management API shows them, a list of them.

    A list of outputs pairs with the inputs by position when the lengths match;
    otherwise each sample carries its input alone rather than a guessed output.
    """
    if isinstance(inputs, dict):
        return [LiveDataSample(input=inputs, output=outputs)]
    if not isinstance(inputs, list):
        return []
    rows = [row for row in inputs if isinstance(row, dict)]
    paired = isinstance(outputs, list) and len(outputs) == len(rows)
    return [LiveDataSample(input=row, output=outputs[n] if paired else None) for n, row in enumerate(rows)]
