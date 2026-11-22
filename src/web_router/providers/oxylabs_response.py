"""An Oxylabs scrape's answer as a live data response: each result's parsed fields are its records.

Both of Oxylabs' APIs answer with ``results``, one per page, and differ in
where a result keeps its fields and fetch, and where the job's id is; an
:class:`Answer` names those places, and :func:`to_response` walks either.

- The Web API (:data:`WEB_API`): a run asks for ``output: ["json"]``, so each
  result carries the scraper's fields under ``json`` -- one object, or a list
  of them -- and its fetch under ``metadata``.
- The Web Scraper API (:data:`WEB_SCRAPER_API`): each result's parsed page is
  under ``content``, with its ``parse_status_code`` and the parser's
  ``_errors`` or ``_warnings`` beside the fields; the fetch is the result's
  other keys.

A page that was not fetched, or that came back with no fields, raises: a run
that brings nothing back is not a success.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from web_router.constants_live_data_vendors import (
    MSG_OXYLABS_NO_FIELDS,
    MSG_OXYLABS_NOT_FETCHED,
    OXYLABS_FAILED_FETCH_FLOOR,
)
from web_router.constants_oxylabs_classic import (
    MSG_OXYLABS_NOT_PARSED,
    OXYLABS_CLASSIC_CONTENT,
    OXYLABS_PARSE_NOTES,
    OXYLABS_PARSE_STATUS,
    OXYLABS_PARSED,
)
from web_router.exceptions import ProviderError
from web_router.types_live_data import LiveDataRequest, LiveDataResponse

Records = Callable[[str, LiveDataRequest, dict[str, Any]], list[dict[str, Any]]]


@dataclass(frozen=True)
class Answer:
    """Where one Oxylabs API's answer keeps each result's records and fetch details, and the job's id."""

    records: Records
    """One result's records; raises for a page not fetched or with none."""
    meta: Callable[[dict[str, Any]], dict[str, Any]]
    """One result's fetch details, ``url`` and ``status_code`` among them: everything but its records."""
    job: str
    """The answer's key for the job, kept whole in ``extra``."""
    job_id: str
    """The job's key for its id, the response's ``request_id``."""


def _not_fetched(provider: str, request: LiveDataRequest, status: Any, url: Any) -> None:
    if isinstance(status, int) and status >= OXYLABS_FAILED_FETCH_FLOOR:
        raise ProviderError(provider, MSG_OXYLABS_NOT_FETCHED.format(source=request.source, status=status, url=url))


def _scraped(provider: str, request: LiveDataRequest, result: dict[str, Any]) -> list[dict[str, Any]]:
    """A Web API result's parsed fields."""
    fields, metadata = result.get("json"), result.get("metadata") or {}
    _not_fetched(provider, request, metadata.get("status_code"), metadata.get("url"))
    rows = [fields] if isinstance(fields, dict) else fields if isinstance(fields, list) else []
    parsed = [row for row in rows if isinstance(row, dict) and row]
    if not parsed:
        raise ProviderError(provider, MSG_OXYLABS_NO_FIELDS.format(source=request.source, url=metadata.get("url")))
    return parsed


def _decoded(content: Any) -> Any:
    """A source answered in JSON of its own may send it as a string (``google_trends_explore``): read it."""
    if not isinstance(content, str):
        return content
    try:
        return json.loads(content)
    except ValueError:
        return content


def _parsed(provider: str, request: LiveDataRequest, result: dict[str, Any]) -> list[dict[str, Any]]:
    """A Web Scraper API result's parsed page, its parse status and notes left out; raises for one not parsed."""
    status, url, content = result.get("status_code"), result.get("url"), _decoded(result.get(OXYLABS_CLASSIC_CONTENT))
    _not_fetched(provider, request, status, url)
    if not isinstance(content, dict):
        raise ProviderError(provider, MSG_OXYLABS_NO_FIELDS.format(source=request.source, url=url))
    code = content.get(OXYLABS_PARSE_STATUS)
    fields = {key: value for key, value in content.items() if key not in (OXYLABS_PARSE_STATUS, *OXYLABS_PARSE_NOTES)}
    if (code is not None and code not in OXYLABS_PARSED) or not fields:
        raise ProviderError(provider, MSG_OXYLABS_NOT_PARSED.format(source=request.source, code=code, url=url))
    return [fields]


def _parsed_meta(row: dict[str, Any]) -> dict[str, Any]:
    """A Web Scraper API result's fetch details, its parse status and the parser's errors or warnings."""
    content = _decoded(row.get(OXYLABS_CLASSIC_CONTENT))
    meta = {key: value for key, value in row.items() if key != OXYLABS_CLASSIC_CONTENT}
    if isinstance(content, dict):
        meta |= {key: content[key] for key in (OXYLABS_PARSE_STATUS, *OXYLABS_PARSE_NOTES) if key in content}
    return meta


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = row.get("metadata") or {}
    return metadata


WEB_API = Answer(records=_scraped, meta=_metadata, job="metadata", job_id="request_id")
"""The Web API's answer: ``metadata.timestamp`` stays in ``extra``, as it is not documented as the fetch time."""
WEB_SCRAPER_API = Answer(records=_parsed, meta=_parsed_meta, job="job", job_id="id")
"""The Web Scraper API's answer: a page parsed in part is still records, its parse status and the parser's errors
in its ``extra`` entry, so a field holding an error string is not taken for the page's own text. The job's
timestamps stay in ``extra``, none being the fetch time."""


def to_response(provider: str, request: LiveDataRequest, payload: Any, answer: Answer = WEB_API) -> LiveDataResponse:
    """Every result's records, read the way ``answer`` says; the job and each result's fetch stay in ``extra``."""
    body = payload if isinstance(payload, dict) else {}
    results = [row for row in body.get("results") or [] if isinstance(row, dict)]
    if not results:
        raise ProviderError(provider, MSG_OXYLABS_NO_FIELDS.format(source=request.source, url=None))
    records = [record for result in results for record in answer.records(provider, request, result)]
    first, job = answer.meta(results[0]), body.get(answer.job) or {}
    return LiveDataResponse(
        provider=provider,
        source=request.source,
        inputs=dict(request.inputs),
        records=records,
        url=first.get("url"),
        status_code=first.get("status_code"),
        request_id=str(job[answer.job_id]) if job.get(answer.job_id) is not None else None,
        extra={answer.job: job, "results": [answer.meta(row) for row in results]},
        raw=payload,
    )
