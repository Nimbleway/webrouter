"""Nimble's Extract Template answers, shaped as live data: a run's envelope as records, a template as a source.

Kept apart from :mod:`web_router.providers.nimble_live_data`, which is left
with the SDK calls. Each function takes the provider's name, so nothing here
needs a client.
"""

from __future__ import annotations

from typing import Any

from web_router.constants import (
    NIMBLE_EXTRACT_FORMATS,
    NIMBLE_LIVE_DATA_ERROR_FIELD,
    NIMBLE_LIVE_DATA_ERROR_SHAPE,
    NIMBLE_LIVE_DATA_FAILED_STATUS_FLOOR,
    NIMBLE_LIVE_DATA_PARSING_ERROR_STATUS,
    NIMBLE_LIVE_DATA_PARSING_SUCCESS_STATUS,
    NIMBLE_LIVE_DATA_RECORD_FIELD,
    NIMBLE_LIVE_DATA_RECORD_SHAPE,
    NIMBLE_LIVE_DATA_STATUS_FIELD,
    NIMBLE_LIVE_DATA_SUCCESS_STATUS,
)
from web_router.constants_live_data import (
    MSG_LIVE_DATA_BAD_STATUS,
    MSG_LIVE_DATA_NO_RECORD,
    MSG_LIVE_DATA_PAGE_FAILED,
    MSG_LIVE_DATA_PARSING_FAILED,
)
from web_router.exceptions import ProviderError
from web_router.providers._utils import compact
from web_router.providers.nimble import _as_int
from web_router.types import ExtractedDocument
from web_router.types_live_data import (
    LiveDataRequest,
    LiveDataResponse,
    LiveDataSample,
    LiveDataSource,
    LiveDataSourcePage,
)

_SOURCE_MAPPED = frozenset({"name", "created_at", "updated_at", "published_version"})
_SOURCE_METADATA_MAPPED = frozenset({"description", "domain", "entity_type"})


def to_response(provider: str, request: LiveDataRequest, payload: dict[str, Any]) -> LiveDataResponse:
    status = payload.get("status")
    data = payload.get("data") or {}

    # As for an extract: a block or a fatal fetch error is a 200 whose
    # `status` says so, and a record cannot have been parsed from nothing.
    if status != NIMBLE_LIVE_DATA_SUCCESS_STATUS:
        detail = MSG_LIVE_DATA_BAD_STATUS.format(source=request.source, status=status)
        raise ProviderError(provider, "; ".join([detail, *(payload.get("warnings") or [])]))

    # The envelope says `success` whenever the fetch completed, a 404 page
    # included, and the template then parses the error page. What it
    # parsed is not the record the template promises.
    status_code = _as_int(payload.get("status_code"))
    if status_code is not None and status_code >= NIMBLE_LIVE_DATA_FAILED_STATUS_FLOOR:
        detail = MSG_LIVE_DATA_PAGE_FAILED.format(source=request.source, status=status_code)
        raise ProviderError(provider, "; ".join([detail, *(payload.get("warnings") or [])]))

    record = _record(provider, request, data.get("parsing"))
    document = _document(request, payload, data)

    return LiveDataResponse(
        provider=provider,
        source=request.source,
        inputs=dict(request.inputs),
        data=record,
        url=payload.get("url"),
        document=document,
        status_code=_as_int(payload.get("status_code")),
        request_id=payload.get("task_id"),
        # Read rather than popped, so `raw` keeps everything; the page
        # body and the record are left out because each has its own field.
        extra=compact(
            {
                "metadata": payload.get("metadata"),
                "pagination": payload.get("pagination"),
                **{k: v for k, v in data.items() if k not in ("parsing", *NIMBLE_EXTRACT_FORMATS.values())},
            }
        ),
        raw=payload,
    )


def _record(provider: str, request: LiveDataRequest, parsing: Any) -> dict[str, Any]:
    """The record under ``data.parsing``, in whichever of its three shapes Nimble sent it.

    See ``NIMBLE_LIVE_DATA_PARSING_SUCCESS_STATUS``: the record may arrive
    bare, or wrapped beside a status that says it parsed, or replaced by
    one that says it did not.

    Raises:
        ProviderError: when the template reports that it failed to parse
            the page, or the page came back with no record at all.
    """
    if not isinstance(parsing, dict) or not parsing:
        raise ProviderError(provider, MSG_LIVE_DATA_NO_RECORD.format(source=request.source))
    # A wrapper is exactly the SDK's two fields and nothing else: a record
    # whose own schema names `status` and `entities` beside other fields is
    # still the record, and unwrapping it would lose the others.
    reported = parsing.get(NIMBLE_LIVE_DATA_STATUS_FIELD)
    if set(parsing) == NIMBLE_LIVE_DATA_ERROR_SHAPE and reported == NIMBLE_LIVE_DATA_PARSING_ERROR_STATUS:
        error = parsing[NIMBLE_LIVE_DATA_ERROR_FIELD]
        raise ProviderError(provider, MSG_LIVE_DATA_PARSING_FAILED.format(source=request.source, error=error))
    if (
        set(parsing) == NIMBLE_LIVE_DATA_RECORD_SHAPE
        and reported == NIMBLE_LIVE_DATA_PARSING_SUCCESS_STATUS
        and isinstance(parsing[NIMBLE_LIVE_DATA_RECORD_FIELD], dict)
    ):
        return dict(parsing[NIMBLE_LIVE_DATA_RECORD_FIELD])
    return dict(parsing)


def _document(request: LiveDataRequest, payload: dict[str, Any], data: dict[str, Any]) -> ExtractedDocument | None:
    """The page beside the record, only when a format asked for it."""
    if request.format is None:
        return None
    return ExtractedDocument(
        url=payload.get("url") or "",
        content=data.get(NIMBLE_EXTRACT_FORMATS[request.format]) or "",
        format=request.format,
        status_code=_as_int(payload.get("status_code")),
    )


def to_page(provider: str, payload: dict[str, Any]) -> LiveDataSourcePage:
    return LiveDataSourcePage(
        provider=provider,
        sources=[to_source(provider, item) for item in payload.get("items") or []],
        total=_as_int(payload.get("total")),
        offset=_as_int(payload.get("offset")),
        limit=_as_int(payload.get("limit")),
        raw=payload,
    )


def to_source(provider: str, item: dict[str, Any]) -> LiveDataSource:
    """One template as Nimble describes it: the published version carries everything a run needs.

    A listing's rows carry the published version too, schemas and samples
    included, so a page of the catalogue describes each template as fully
    as a describe does.
    """
    version = item.get("published_version") or {}
    metadata = version.get("metadata") or {}
    return LiveDataSource(
        provider=provider,
        name=item.get("name") or "",
        description=metadata.get("description") or "",
        domain=metadata.get("domain"),
        entity_type=metadata.get("entity_type"),
        input_schema=version.get("input_schema"),
        output_schema=version.get("output_schema"),
        samples=[
            LiveDataSample(input=inputs, output=sample.get("output"))
            for sample in version.get("samples") or []
            if (inputs := _sample_input(sample)) is not None
        ],
        version=_as_int(version.get("version_number")),
        published=bool(version),
        created_at=item.get("created_at"),
        updated_at=item.get("updated_at"),
        extra=compact(
            {
                **{k: v for k, v in item.items() if k not in _SOURCE_MAPPED},
                **{k: v for k, v in metadata.items() if k not in _SOURCE_METADATA_MAPPED},
            }
        ),
        raw=item,
    )


def _sample_input(sample: object) -> dict[str, Any] | None:
    """A recorded sample's input, or ``None`` when the sample is malformed and is skipped.

    A missing or null input is a fixed page's, which takes none, so it is
    ``{}``; anything else that is not a mapping, even an empty one, is not
    an input the backend published.
    """
    if not isinstance(sample, dict):
        return None
    inputs = sample.get("input")
    if inputs is None:
        return {}
    return inputs if isinstance(inputs, dict) else None
