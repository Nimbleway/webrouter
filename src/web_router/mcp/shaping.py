"""Turning a library response into something a context window can hold.

This is the one place the MCP layer is allowed to differ from the library. The
library's rule is that nothing is thrown away -- full page bodies, and the
backend's untouched payload under ``raw``. Inside a model's context that rule
stops being a virtue: a single deep search can be larger than the window it is
being read in.

So a shaped result keeps the fields a reader acts on, cuts long text at a word
boundary with a visible marker, never inlines ``raw``, and says in the result
itself that it was shortened and which handle gets the rest. Nothing is lost --
the whole payload stays in the session's :class:`HandleStore` until it is
evicted, and ``fetch_raw`` hands it back verbatim.

Browse is the one exception to "verbatim". A task payload can carry the URL of
the session the agent ran in, and some backends put a key in it, so a browse
result -- shaped or raw -- goes through :func:`redact` before it leaves.
"""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from web_router.constants import (
    ENV_MCP_MAX_CHARS,
    MCP_ANSWER_SHARE,
    MCP_HANDLE_LIMIT,
    MCP_HANDLE_TEMPLATE,
    MCP_JSON_INDENT,
    MCP_MAX_RESPONSE_CHARS,
    MCP_MIN_FIELD_CHARS,
    MCP_MSG_DROPPED,
    MCP_MSG_OVERSIZED,
    MCP_MSG_REDACTED,
    MCP_MSG_TRUNCATED,
    MCP_REDACT_DEVTOOLS_PATTERN,
    MCP_REDACT_PLURAL,
    MCP_REDACT_QUERY_PARAMS,
    MCP_REDACT_SECRET_KEY_SUFFIXES,
    MCP_REDACT_SECRET_KEYS,
    MCP_REDACT_SESSION_ID_KEY_SUFFIXES,
    MCP_REDACT_SESSION_KEY_FRAGMENTS,
    MCP_REDACT_SESSION_KEY_WORDS,
    MCP_REDACT_URL_SCHEMES,
    MCP_REDACT_URL_SESSION_WORDS,
    MCP_REDACTED,
    MCP_TRUNCATION_MARKER,
    MCP_WORD_SEARCH_WINDOW,
)
from web_router.providers.base import BrowseJob, ResearchJob
from web_router.types import (
    BrowseProgress,
    BrowseResponse,
    ExtractResponse,
    ResearchProgress,
    ResearchResponse,
    SearchResponse,
    Template,
    TemplatePage,
    TemplateResponse,
)

_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")
_DEVTOOLS = re.compile(MCP_REDACT_DEVTOOLS_PATTERN)
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])|([A-Z]+)([A-Z][a-z])")
_WORD = re.compile(r"[a-z0-9]+")


def max_response_chars() -> int:
    """How large one shaped result may be, from the environment or the default.

    An unparseable value falls back to the default rather than raising: a stdio
    server that refuses to start over a typo in a client config file is worse
    than one that quietly uses its own number.
    """
    try:
        return int(os.environ[ENV_MCP_MAX_CHARS])
    except (KeyError, ValueError):
        return MCP_MAX_RESPONSE_CHARS


def render(payload: dict[str, Any]) -> str:
    """Serialize a shaped result the way the tool returns it.

    The same function the budget is measured against, so the cap counts the
    characters that actually reach the client rather than an estimate of them.
    """
    return json.dumps(payload, indent=MCP_JSON_INDENT, ensure_ascii=False, default=str)


def truncate(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` characters at a word boundary, marking where it was cut."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    # Only a boundary near the cut will do: honouring one further back would
    # throw away text that fits, which is a worse trade than a split word.
    boundary = head.rfind(" ", limit - MCP_WORD_SEARCH_WINDOW)
    if boundary > 0:
        head = head[:boundary]
    return head.rstrip() + MCP_TRUNCATION_MARKER


@dataclass
class ToolOutput:
    """A result that is more than one JSON object: a snapshot's tree as its own text, or a screenshot.

    ``payload`` is rendered as every other tool's result is; ``text`` follows it
    as a second, unescaped text block, and ``png`` as image content.
    """

    payload: dict[str, Any]
    text: str | None = None
    png: bytes | None = None


@dataclass
class Record:
    """What one call left behind: its handle, and everything the shaped result dropped."""

    handle: str
    tool: str
    raw: Any = None
    job: ResearchJob | BrowseJob | None = None


class HandleStore:
    """The session's handles, oldest evicted first.

    Bounded because an MCP session outlives any one call: a conversation that
    searches two hundred times would otherwise hold two hundred untouched
    payloads for as long as the client stays connected.
    """

    def __init__(self, limit: int = MCP_HANDLE_LIMIT) -> None:
        self.limit = limit
        self._records: OrderedDict[str, Record] = OrderedDict()
        self._issued = 0

    def add(self, tool: str, *, raw: Any = None, job: ResearchJob | BrowseJob | None = None) -> Record:
        """Store what a call left behind and return the record, handle included."""
        self._issued += 1
        record = Record(handle=MCP_HANDLE_TEMPLATE.format(tool=tool, number=self._issued), tool=tool, raw=raw, job=job)
        self._records[record.handle] = record
        while len(self._records) > self.limit:
            self._records.popitem(last=False)
        return record

    def get(self, handle: str) -> Record | None:
        """The record under ``handle``, or ``None`` once it has been evicted."""
        return self._records.get(handle)

    def __len__(self) -> int:
        return len(self._records)


def shape_search(response: SearchResponse, handle: str, *, budget: int) -> dict[str, Any]:
    """A search response, cut to fit.

    ``description`` is dropped on purpose: the library defines ``content`` as
    the fullest text available and falls back to the description when there is
    no body, so carrying both would spend half the budget saying the same thing
    twice.
    """
    results = [
        {
            "url": result.url,
            "title": result.title,
            "content": result.content,
            "score": result.score,
            "published_date": result.published_date,
        }
        for result in response.results
    ]
    # A synthesized answer is worth more per character than any one result, so
    # it gets its own share rather than competing with them for the budget.
    answer = truncate(response.answer, int(budget * MCP_ANSWER_SHARE)) if response.answer else response.answer
    payload: dict[str, Any] = {
        "provider": response.provider,
        "query": response.query,
        "answer": answer,
        "result_count": len(response.results),
        "results": results,
    }
    return _fit(payload, results, ("content",), handle=handle, budget=budget, shortened=answer != response.answer)


def shape_extract(response: ExtractResponse, handle: str, *, budget: int) -> dict[str, Any]:
    """An extract response, cut to fit. Failures are kept whole -- they are short and they matter."""
    documents = [
        {
            "url": document.url,
            "title": document.title,
            "format": document.format,
            "status_code": document.status_code,
            "content": document.content,
        }
        for document in response.documents
    ]
    payload: dict[str, Any] = {
        "provider": response.provider,
        "document_count": len(response.documents),
        "documents": documents,
        "failures": [{"url": failure.url, "error": failure.error} for failure in response.failures],
    }
    return _fit(payload, documents, ("content",), handle=handle, budget=budget)


def shape_templates(page: TemplatePage, handle: str, *, budget: int) -> dict[str, Any]:
    """One page of a template catalogue, cut to fit: names and what each extracts, never the schemas.

    A catalogue runs to thousands, so each row is the few words a model picks
    a template by; ``template`` returns the schemas for the one it picked.
    """
    templates = [_template_row(template) for template in page.templates]
    payload: dict[str, Any] = {
        "provider": page.provider,
        "total": page.total,
        "offset": page.offset,
        "limit": page.limit,
        "templates": templates,
    }
    fitted = _fit(payload, templates, ("description",), handle=handle, budget=budget)
    # Counted after the fit, which may have dropped rows: the count is of what is shown.
    fitted["template_count"] = len(templates)
    return fitted


def shape_template(template: Template, handle: str, *, budget: int) -> dict[str, Any]:
    """One template with its schemas, cut to fit.

    The schemas are never shortened: a run has to match ``input_schema``
    exactly, and a cut schema would send the model guessing at the inputs it
    dropped. One that does not fit even on its own is left out whole, with a
    note naming the handle that holds it, rather than inlined over the budget.
    """
    row = {
        **_template_row(template),
        "version": template.version,
        "input_schema": template.input_schema,
        "output_schema": template.output_schema,
    }
    payload: dict[str, Any] = {"provider": template.provider, "template": row}
    fitted = _fit(payload, [row], ("description",), handle=handle, budget=budget)
    return _bounded(fitted, ((row, "output_schema"), (row, "input_schema")), handle=handle, budget=budget)


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


def shape_research(response: ResearchResponse, handle: str, *, budget: int) -> dict[str, Any]:
    """A finished research run, cut to fit.

    ``structured`` is left alone: it is the object the caller's own schema
    asked for, and trimming it would hand back something that no longer
    validates against the schema they supplied.
    """
    answer_budget = int(budget * MCP_ANSWER_SHARE)
    answer = truncate(response.answer, answer_budget) if response.answer else response.answer
    citations = [
        {"url": citation.url, "title": citation.title, "field": citation.field, "snippet": citation.snippet}
        for citation in response.citations
    ]
    payload: dict[str, Any] = {
        "provider": response.provider,
        "query": response.query,
        "state": "completed",
        "outcome": response.outcome,
        "answer": answer,
        "structured": response.structured,
        "citations": citations,
    }
    shortened = answer != response.answer
    return _fit(payload, citations, ("snippet",), handle=handle, budget=budget, shortened=shortened)


def shape_browse(response: BrowseResponse, handle: str, *, budget: int) -> dict[str, Any]:
    """A finished browse task, cut to fit and redacted.

    Shaped as a research run is, with the agent's steps where the citations
    would be: each step's ``detail`` is the text that gets cut, and a step's
    ``extra`` is left behind the handle with the rest of the payload.
    ``request_id`` is left out as research leaves it out -- the progress shape
    already carries the job id.
    """
    answer_budget = int(budget * MCP_ANSWER_SHARE)
    answer = truncate(response.answer, answer_budget) if response.answer else response.answer
    steps = [
        {"action": step.action, "url": step.url, "detail": step.detail, "screenshot_url": step.screenshot_url}
        for step in response.steps
    ]
    payload: dict[str, Any] = {
        "provider": response.provider,
        "task": response.task,
        "state": "completed",
        "outcome": response.outcome,
        "answer": answer,
        "structured": response.structured,
        "recording_url": response.recording_url,
        "step_count": len(response.steps),
        "steps": steps,
    }
    # Redacted before fitting, so the budget is measured on what is sent.
    payload, count = redact(payload)
    shortened = answer != response.answer
    return _fit(
        payload,
        payload["steps"],
        ("detail",),
        handle=handle,
        budget=budget,
        shortened=shortened,
        extra_note=redaction_note(count),
    )


def redact(value: Any) -> tuple[Any, int]:
    """A copy of ``value`` with every browser-session URL and credential replaced, and how many were.

    Taken out, anywhere in the structure: everything beneath a key that names a
    credential or a backend's session id; any URL beneath a key that names a
    way into a running browser (a live view, a debugger, a CDP, websocket or
    connection endpoint); and any URL that is one on its face -- a websocket scheme, a userinfo part, a
    credential in its query, or a session word in its host or path. URLs are
    found inside longer strings too, because an agent's own notes can quote
    one. The rules are the ``MCP_REDACT_*`` tables in ``constants.py``.
    """
    return _redact(value, None)


_SECRET = "secret"
_SESSION_ID = "session_id"
_SESSION = "session"
_WHOLE = frozenset({_SECRET, _SESSION_ID})


def _redact(value: Any, context: str | None) -> tuple[Any, int]:
    if isinstance(value, dict):
        total = 0
        cleaned: dict[Any, Any] = {}
        for name, item in value.items():
            # A credential key outranks a session one, and neither is forgotten
            # by a plainer key further down: {"live": {"url": ...}} is still a live view.
            inner = context if context in _WHOLE else (_classify(str(name)) or context)
            cleaned[name], count = _redact(item, inner)
            total += count
        return cleaned, total
    if isinstance(value, (list, tuple)):
        items = [_redact(item, context) for item in value]
        cleaned_items = [item for item, _ in items]
        return (tuple(cleaned_items) if isinstance(value, tuple) else cleaned_items), sum(n for _, n in items)
    if context == _SESSION_ID and isinstance(value, (int, float)) and not isinstance(value, bool):
        return MCP_REDACTED, 1
    if not isinstance(value, str) or not value:
        return value, 0
    if context in _WHOLE or (context == _SESSION and _URL.search(value)):
        return MCP_REDACTED, 1
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        if not _is_sensitive_url(match.group(0)):
            return match.group(0)
        count += 1
        return MCP_REDACTED

    value = _URL.sub(_replace, value)
    value, devtools = _DEVTOOLS.subn(MCP_REDACTED, value)
    return value, count + devtools


def _classify(key: str) -> str | None:
    """Whether a field's name marks its contents as a credential, a session's id, a way into one, or neither."""
    words = _key_words(key)
    joined = "".join(words)
    if any(
        form in MCP_REDACT_SECRET_KEYS or form.endswith(MCP_REDACT_SECRET_KEY_SUFFIXES) for form in _singular(joined)
    ):
        return _SECRET
    if any(form.endswith(MCP_REDACT_SESSION_ID_KEY_SUFFIXES) for form in _singular(joined)):
        return _SESSION_ID
    if any(not MCP_REDACT_SESSION_KEY_WORDS.isdisjoint(_singular(word)) for word in words) or any(
        fragment in joined for fragment in MCP_REDACT_SESSION_KEY_FRAGMENTS
    ):
        return _SESSION
    return None


def _singular(word: str) -> set[str]:
    return {word, word.removesuffix(MCP_REDACT_PLURAL)}


def _key_words(key: str) -> list[str]:
    """``wsEndpoint`` -> ``["ws", "endpoint"]``; ``live_view_url`` -> ``["live", "view", "url"]``."""
    spaced = _CAMEL_BOUNDARY.sub(lambda match: " ".join(part for part in match.groups() if part), key)
    return _WORD.findall(spaced.lower())


def _is_sensitive_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        userinfo = parts.username or parts.password
        host = parts.hostname or ""
    except ValueError:
        # Unparseable is not proof of safety; a URL this layer cannot read is not sent on.
        return True
    if parts.scheme.lower() in MCP_REDACT_URL_SCHEMES or userinfo:
        return True
    if not MCP_REDACT_URL_SESSION_WORDS.isdisjoint(_WORD.findall(f"{host} {parts.path}".lower())):
        return True
    params = parse_qsl(parts.query, keep_blank_values=True) + parse_qsl(parts.fragment, keep_blank_values=True)
    return any(_normalize(name) in MCP_REDACT_QUERY_PARAMS for name, _ in params)


def _normalize(name: str) -> str:
    return name.lower().replace("_", "").replace("-", "")


def redaction_note(count: int) -> str:
    """The sentence a redacted result carries, empty when nothing was redacted."""
    return MCP_MSG_REDACTED.format(count=count, marker=MCP_REDACTED) if count else ""


def shape_progress(progress: ResearchProgress | BrowseProgress, handle: str) -> dict[str, Any]:
    """A job that has not answered yet -- or one that stopped without answering."""
    return {
        "handle": handle,
        "job_id": progress.job_id,
        "state": progress.state,
        "detail": progress.detail,
        "truncated": False,
    }


def shape_pending(job: ResearchJob | BrowseJob, handle: str, *, wait: float, note: str) -> dict[str, Any]:
    """A job still going when the tool's wait ran out. ``note`` says which tool polls it."""
    return {
        "handle": handle,
        "job_id": job.job_id,
        "provider": job.provider,
        "state": job.state,
        "truncated": False,
        "note": note.format(wait=wait, handle=handle),
    }


def _fit(
    payload: dict[str, Any],
    items: list[dict[str, Any]],
    text_keys: tuple[str, ...],
    *,
    handle: str,
    budget: int,
    shortened: bool = False,
    extra_note: str = "",
) -> dict[str, Any]:
    """Shrink a shaped payload until it serializes inside ``budget``, and say so if it did.

    ``extra_note`` is appended to the note, and sets one on its own when nothing
    was cut; it is inside the loop so the budget counts it.

    Long text goes first, each item getting an equal share of the budget. Only
    when that is not enough are whole items dropped, from the end -- the least
    useful place to cut a ranked list. The last item is never dropped: a result
    listing nothing at all tells the reader less than a heavily cut one, so it
    is squeezed further instead.
    """
    share = max(MCP_MIN_FIELD_CHARS, budget // max(len(items), 1))
    for item in items:
        shortened = _shorten(item, text_keys, share) or shortened

    dropped = 0
    total = len(items)
    while True:
        payload["handle"] = handle
        payload["truncated"] = shortened or dropped > 0
        note = ""
        if payload["truncated"]:
            dropped_note = MCP_MSG_DROPPED.format(dropped=dropped, total=total) if dropped else ""
            note = MCP_MSG_TRUNCATED.format(marker=MCP_TRUNCATION_MARKER, dropped=dropped_note, handle=handle)
        note = (note + extra_note).strip()
        if note:
            payload["note"] = note

        overflow = len(render(payload)) - budget
        if overflow <= 0:
            return payload
        if len(items) > 1:
            items.pop()
            dropped += 1
        # The marker is added back by the cut, so its own length has to come out
        # of the request or a shrink that small makes the text no shorter.
        elif not any(
            _shorten(item, text_keys, _room(item, text_keys) - overflow - len(MCP_TRUNCATION_MARKER)) for item in items
        ):
            # Nothing left to give: what remains is the shape itself, which is
            # worth more than staying under a self-imposed cap.
            return payload
        else:
            shortened = True


def _shorten(item: dict[str, Any], text_keys: tuple[str, ...], limit: int) -> bool:
    """Cut every text field of one item to ``limit``. ``True`` if anything was actually cut."""
    cut = False
    for key in text_keys:
        text = item.get(key)
        if text is None:
            # Absent stays absent: an empty string would claim the backend sent one.
            continue
        item[key] = truncate(text, max(limit, 0))
        cut = cut or item[key] != text
    return cut


def _room(item: dict[str, Any], text_keys: tuple[str, ...]) -> int:
    """How long this item's longest text field is, which is what a further cut has to work with."""
    return max((len(item.get(key) or "") for key in text_keys), default=0)
