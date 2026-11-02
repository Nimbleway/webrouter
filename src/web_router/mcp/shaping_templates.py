"""Shaping the template tools' results: a catalogue page, one template, one run.

Kept beside :mod:`web_router.mcp.shaping`, whose budget helpers it uses. A
schema or a recorded sample is never cut, since half of one is not one a run
can copy: what does not fit is left out whole, with a note naming the handle
that holds it.
"""

from __future__ import annotations

import json
from typing import Any

from web_router.constants import MCP_JSON_INDENT, MCP_MIN_FIELD_CHARS, MCP_MSG_OVERSIZED
from web_router.mcp.shaping import _fit, render
from web_router.types import Template, TemplatePage, TemplateResponse


def shape_templates(page: TemplatePage, handle: str, *, budget: int) -> dict[str, Any]:
    """One page of a template catalogue, cut to fit: names, what each extracts and what each takes, never the schemas.

    A catalogue runs to thousands, so each row is the few words a model picks
    a template by, plus the names of the inputs a run must pass and one
    recorded input, enough to tell a URL-keyed template from an id-keyed one
    without a second call; ``template`` returns the schemas and the full
    samples for the one it picked.
    """
    templates = [_template_listing_row(template) for template in page.templates]
    payload: dict[str, Any] = {
        "provider": page.provider,
        "total": page.total,
        "offset": page.offset,
        "limit": page.limit,
        "templates": templates,
    }
    # A recorded input is copied whole or not at all, so one larger than its
    # row's share of the budget is left out rather than crowding out the rows.
    share = max(MCP_MIN_FIELD_CHARS, budget // max(len(templates), 1))
    notes = []
    for row in templates:
        size = _size(row["sample_inputs"])
        if size > share:
            row["sample_inputs"] = None
            notes.append(MCP_MSG_OVERSIZED.format(field=f"{row['name']}'s sample_inputs", size=size, handle=handle))
    extra_note = " " + " ".join(notes) if notes else ""
    fitted = _fit(payload, templates, ("description",), handle=handle, budget=budget, extra_note=extra_note)
    fitted["truncated"] = fitted["truncated"] or bool(notes)
    fitted = _bounded(fitted, tuple((row, "sample_inputs") for row in templates), handle=handle, budget=budget)
    # Counted after the fit, which may have dropped rows: the count is of what is shown.
    fitted["template_count"] = len(templates)
    return fitted


def shape_template(template: Template, handle: str, *, budget: int) -> dict[str, Any]:
    """One template with its schemas and samples, cut to fit.

    The schemas are never shortened: a run has to match ``input_schema``
    exactly, and a cut schema would send the model guessing at the inputs it
    dropped. The samples go the same way: a recorded input with half its
    fields is not one a run can copy. Whichever does not fit even on its own
    is left out whole, largest first, with a note naming the handle that
    holds it, rather than inlined over the budget.
    """
    row = {
        **_template_row(template),
        "version": template.version,
        "input_schema": template.input_schema,
        "output_schema": template.output_schema,
        "samples": [sample.model_dump() for sample in template.samples],
    }
    payload: dict[str, Any] = {"provider": template.provider, "template": row}
    fitted = _fit(payload, [row], ("description",), handle=handle, budget=budget)
    return _bounded(
        fitted, ((row, "samples"), (row, "output_schema"), (row, "input_schema")), handle=handle, budget=budget
    )


def shape_template_run(response: TemplateResponse, handle: str, *, budget: int) -> dict[str, Any]:
    """A template run, cut to fit. The record is never shortened, as a research run's ``structured`` is not.

    The record is what the caller asked for, shaped by the template's own
    schema; the page beside it, when one was asked for, is the one thing
    long enough to cut. A record that does not fit even on its own is left
    out whole, with a note naming the handle that holds it.
    """
    page = (
        {
            "url": response.document.url,
            "format": response.document.format,
            "status_code": response.document.status_code,
            "content": response.document.content,
        }
        if response.document is not None
        else None
    )
    # The inputs are not echoed: the caller sent them, and unbounded they
    # could fill the budget on their own.
    payload: dict[str, Any] = {
        "provider": response.provider,
        "template": response.template,
        "url": response.url,
        "status_code": response.status_code,
        "data": response.data,
        "document": page,
    }
    fitted = _fit(payload, [page] if page is not None else [], ("content",), handle=handle, budget=budget)
    return _bounded(fitted, ((payload, "data"),), handle=handle, budget=budget)


def _bounded(
    payload: dict[str, Any], fields: tuple[tuple[dict[str, Any], str], ...], *, handle: str, budget: int
) -> dict[str, Any]:
    """Leave out, largest first, each untrimmable field until the payload fits, saying so each time.

    For a field that must stay whole to mean anything -- a schema, a record --
    the choice is between inlining it over the budget and leaving it out with
    a pointer to the raw payload; the second keeps a single result from
    filling a context window. The largest goes first so the smaller ones
    stay whenever dropping it is enough.
    """
    by_size = sorted(fields, key=lambda field: _size(field[0].get(field[1])), reverse=True)
    for container, key in by_size:
        if len(render(payload)) <= budget:
            break
        value = container.get(key)
        if value is None:
            continue
        note = MCP_MSG_OVERSIZED.format(field=key, size=_size(value), handle=handle)
        container[key] = None
        payload["truncated"] = True
        payload["note"] = f"{payload['note']} {note}" if payload.get("note") else note
    return payload


def _size(value: Any) -> int:
    """How many characters a field takes once serialized as the tool returns it; nothing for an absent one."""
    return 0 if value is None else len(json.dumps(value, indent=MCP_JSON_INDENT, ensure_ascii=False, default=str))


def _template_row(template: Template) -> dict[str, Any]:
    return {
        "name": template.name,
        "description": template.description,
        "domain": template.domain,
        "entity_type": template.entity_type,
        "published": template.published,
    }


def _template_listing_row(template: Template) -> dict[str, Any]:
    """A catalogue row: the picking words plus what a run must pass, read from the schema and the first sample.

    ``required_inputs`` is the schema's own ``required`` list, so it is empty
    for a template whose inputs are all optional and ``None`` for one with
    no published schema; ``sample_inputs`` is the first recorded input that
    names any, as the backend published it, never one made up here.
    """
    schema = template.input_schema
    required = schema.get("required") if isinstance(schema, dict) else None
    return {
        **_template_row(template),
        "required_inputs": list(required) if isinstance(required, list) else ([] if isinstance(schema, dict) else None),
        "sample_inputs": next(
            (sample.input for sample in template.samples if sample.input),
            template.samples[0].input if template.samples else None,
        ),
    }
