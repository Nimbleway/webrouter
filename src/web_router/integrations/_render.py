"""What a model reads back from a web-router tool, whichever framework asked.

One layout for every backend, so what varies between providers is what they
found, not how it is laid out -- and one layout for every framework, so an
agent benchmark comparing two of them compares the tools and not their
formatting. Compact JSON, and a field with nothing in it is left out: a model
pays for every character and can act on none of ``"title": ""``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from web_router.constants import (
    INTEGRATION_DOCUMENT_CHARS,
    INTEGRATION_RESULT_CHARS,
    INTEGRATION_TRUNCATION_MARKER,
)
from web_router.integrations._messages import GENERIC_REASON, KIND_REASONS, MODEL_WARNINGS
from web_router.types import Citation, ExtractedDocument, ExtractResponse, SearchResponse
from web_router.types_answer import AnswerResponse
from web_router.types_errors import is_retryable


def render_search(response: SearchResponse, result_chars: int = INTEGRATION_RESULT_CHARS) -> str:
    """A search response in rank order, each result's text cut to ``result_chars``."""
    results = [
        {
            "title": result.title,
            "url": result.url,
            "content": cut_to_budget(result.content, result_chars),
            "published_date": result.published_date,
        }
        for result in response.results
    ]
    return dumps({"answer": response.answer, "results": results})


def render_extract(
    response: ExtractResponse,
    document_chars: int = INTEGRATION_DOCUMENT_CHARS,
    refused: Sequence[dict[str, Any]] = (),
) -> str:
    """An extract response, each page cut to ``document_chars``, then each URL it could not read and why.

    ``refused`` are the URLs the URL policy kept from being sent, in the same
    shape as a backend's failures.
    """
    failures = [failure_row(f.url, f.kind, f.retryable, f.status_code) for f in response.failures]
    documents = [_document(document, document_chars) for document in response.documents]
    return dumps({"documents": documents, "failures": [*failures, *refused] or None})


def render_extract_failure(error: str, failures: Sequence[dict[str, Any]]) -> str:
    """An extract that read no page: what went wrong in one line, then each URL in the shape a partial one has."""
    return dumps({"error": error, "failures": list(failures)})


def failure_row(
    url: str, kind: str | None, retryable: bool | None = None, status_code: int | None = None
) -> dict[str, Any]:
    """One URL that could not be read: why in words, its kind, and whether another try may get it."""
    return {
        "url": url,
        "reason": KIND_REASONS.get(kind or "", GENERIC_REASON),
        "kind": kind,
        "retryable": is_retryable(kind) if retryable is None else retryable,
        "status_code": status_code,
    }


def _document(document: ExtractedDocument, document_chars: int) -> dict[str, Any]:
    """One page: its status, so a model can tell a page from an error, and web-router's own warnings about it."""
    return {
        "url": document.url,
        "title": document.title,
        "status_code": document.status_code,
        "warnings": [warning for warning in document.warnings if warning in MODEL_WARNINGS] or None,
        "content": cut_to_budget(document.content, document_chars),
    }


def render_answer(response: AnswerResponse, result_chars: int = INTEGRATION_RESULT_CHARS) -> str:
    """A written answer whole, then its citations in order, each snippet cut to ``result_chars``.

    The answer is the backend's and short, so it is never cut. A citation the
    answer marks inline carries its marker as ``field``, so the model can tell
    which source backs ``[2]``. ``outcome`` is there only when the backend said
    it stopped early.
    """
    return dumps(
        {
            "answer": response.answer,
            "citations": [_citation(citation, result_chars) for citation in response.citations],
            "outcome": response.outcome if response.outcome != "complete" else None,
        }
    )


def _citation(citation: Citation, result_chars: int) -> dict[str, Any]:
    return {
        "title": citation.title,
        "url": citation.url,
        "field": citation.field,
        "snippet": cut_to_budget(citation.snippet, result_chars),
    }


def dumps(payload: dict[str, Any]) -> str:
    """``payload`` as compact JSON, with every ``None`` or empty-string field left out, at any depth."""
    return json.dumps(_kept(payload), ensure_ascii=False, separators=(",", ":"), default=str)


def _kept(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _kept(item) for key, item in value.items() if item is not None and item != ""}
    if isinstance(value, list):
        return [_kept(item) for item in value]
    return value


def cut_to_budget(text: str, limit: int) -> str:
    """``text`` up to ``limit`` characters, marked where it was cut; ``evaluate()`` reads results through it too."""
    return text if len(text) <= limit else text[:limit].rstrip() + INTEGRATION_TRUNCATION_MARKER
