"""What a model reads back from a web-router tool, whichever framework asked.

One layout for every backend, so what varies between providers is what they
found, not how it is laid out -- and one layout for every framework, so an
agent benchmark comparing two of them compares the tools and not their
formatting.
"""

from __future__ import annotations

import json
from typing import Any

from web_router.constants import (
    INTEGRATION_DOCUMENT_CHARS,
    INTEGRATION_JSON_INDENT,
    INTEGRATION_RESULT_CHARS,
    INTEGRATION_TRUNCATION_MARKER,
)
from web_router.types import Citation, ExtractedDocument, ExtractResponse, SearchResponse
from web_router.types_answer import AnswerResponse


def render_search(response: SearchResponse, result_chars: int = INTEGRATION_RESULT_CHARS) -> str:
    """A search response in rank order, each result's text cut to ``result_chars``."""
    payload: dict[str, Any] = {
        "results": [
            {
                "title": result.title,
                "url": result.url,
                "content": cut_to_budget(result.content, result_chars),
                "published_date": result.published_date,
            }
            for result in response.results
        ]
    }
    if response.answer:
        payload = {"answer": response.answer, **payload}
    return json.dumps(payload, indent=INTEGRATION_JSON_INDENT, ensure_ascii=False, default=str)


def render_extract(response: ExtractResponse, document_chars: int = INTEGRATION_DOCUMENT_CHARS) -> str:
    """An extract response, each page cut to ``document_chars``; failures whole, since they are short and matter."""
    payload = {
        "documents": [_document(document, document_chars) for document in response.documents],
        "failures": [{"url": failure.url, "error": failure.error} for failure in response.failures],
    }
    return json.dumps(payload, indent=INTEGRATION_JSON_INDENT, ensure_ascii=False, default=str)


def _document(document: ExtractedDocument, document_chars: int) -> dict[str, Any]:
    """One page: its status, so a model can tell a page from an error, and any warning about it."""
    row: dict[str, Any] = {"url": document.url, "title": document.title, "status_code": document.status_code}
    if document.warnings:
        row["warnings"] = [cut_to_budget(warning, INTEGRATION_RESULT_CHARS) for warning in document.warnings]
    row["content"] = cut_to_budget(document.content, document_chars)
    return row


def render_answer(response: AnswerResponse, result_chars: int = INTEGRATION_RESULT_CHARS) -> str:
    """A written answer whole, then its citations in order, each snippet cut to ``result_chars``.

    The answer is the backend's and short, so it is never cut. A citation the
    answer marks inline carries its marker as ``field``, so the model can tell
    which source backs ``[2]``. ``outcome`` is there only when the backend said
    it stopped early.
    """
    payload: dict[str, Any] = {
        "answer": response.answer,
        "citations": [_citation(citation, result_chars) for citation in response.citations],
    }
    if response.outcome != "complete":
        payload["outcome"] = response.outcome
    return json.dumps(payload, indent=INTEGRATION_JSON_INDENT, ensure_ascii=False, default=str)


def _citation(citation: Citation, result_chars: int) -> dict[str, Any]:
    row: dict[str, Any] = {"title": citation.title, "url": citation.url}
    if citation.field is not None:
        row["field"] = citation.field
    row["snippet"] = cut_to_budget(citation.snippet, result_chars)
    return row


def cut_to_budget(text: str, limit: int) -> str:
    """``text`` up to ``limit`` characters, marked where it was cut; ``evaluate()`` reads results through it too."""
    return text if len(text) <= limit else text[:limit].rstrip() + INTEGRATION_TRUNCATION_MARKER
