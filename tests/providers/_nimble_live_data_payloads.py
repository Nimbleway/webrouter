"""Recorded-shape Nimble live data payloads the two Nimble test files share. Names, ids and timestamps are synthetic."""

from __future__ import annotations

from typing import Any

TEMPLATE = "books_pdp"
URL = "https://books.example/catalogue/a-light-in-the-attic/index.html"
RECORD = {"title": "A Light in the Attic", "price": "£51.77", "upc": "0000000000000001", "num_reviews": 0}
TASK_ID = "00000000-0000-4000-8000-000000000001"
TEMPLATE_ID = "00000000-0000-4000-8000-000000000002"
VERSION_ID = "00000000-0000-4000-8000-000000000003"


def _run_payload(parsing: Any, **extra: Any) -> dict[str, Any]:
    return {
        "task_id": TASK_ID,
        "url": URL,
        "status": "success",
        "status_code": 200.0,
        "metadata": {"driver": "fetch", "query_duration": 1000.0, "response_parameters": {"input_url": URL}},
        "data": {"markdown": None, "html": None, "links": None, "parsing": parsing},
        "pagination": None,
        "warnings": ["render_options is ignored because render is false"],
        **extra,
    }


def _version(**overrides: Any) -> dict[str, Any]:
    return {
        "id": VERSION_ID,
        "created_at": "2026-01-01T00:00:00Z",
        "name": TEMPLATE,
        "version_number": 1,
        "input_schema": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
        "output_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
        "metadata": {
            "data_source": "books.example",
            "description": "Extracts book product details.",
            "display_name": "Books - Book Detail",
            "domain": "books.example",
            "entity_type": "Product Detail Page (PDP)",
            "tags": ["books", "ecommerce"],
            "vertical": "Ecommerce",
        },
        "samples": [{"input": {"url": URL}, "output": RECORD}],
        **overrides,
    }


def _item(version: dict[str, Any] | None, **overrides: Any) -> dict[str, Any]:
    return {
        "id": TEMPLATE_ID,
        "name": TEMPLATE,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "published_version": version,
        "published_version_id": version["id"] if version else None,
        "privacy": "private",
        "managed_by": None,
        **overrides,
    }
