"""A Nimble research run's citations: its claims' when it ran with trust, else its source index's markers.

Apart from :mod:`web_router.providers.nimble` so that file stops growing.

A run with trust attributes each claim -- a path into the structured answer,
or the callout marker in the prose -- and quotes its sources' excerpts, which
become :attr:`~web_router.Citation.field` and ``snippet``. A run without trust
(``trust: null``, the default) attributes nothing in its payload: its prose
cites inline as ``[2]`` and ends with its own list, ``Source index:``, one
``[N] title -- URL`` line per source. That list is read here, so a source the
prose cites carries its marker in ``field``, and the paragraphs and list items
that cite it in ``extra["answer_passages"]``. Nimble quotes no page then, so
``snippet`` stays empty. The list itself stays in the answer, as written.
"""

from __future__ import annotations

import re
from typing import Any

from web_router.providers._utils import compact
from web_router.types import Citation

_INDEX_HEADING = re.compile(r"^[ \t#*_]*source index[*_]*:?[*_]*[ \t]*$", re.IGNORECASE | re.MULTILINE)
"""The line the prose's own source list starts after, as Nimble writes it: ``Source index:``."""

_INDEX_ENTRY = re.compile(r"^[ \t]*(?:[-*][ \t]*)?\[(\d+)\][^\n]*?(https?://[^\s)\]>]+)", re.MULTILINE)
"""One line of that list: its number, then the first URL on the line."""

ANSWER_PASSAGES = "answer_passages"
"""The ``extra`` key holding the prose's paragraphs that cite a source, for a run without trust."""


def research_citations(answer: str | None, output: dict[str, Any]) -> list[Citation]:
    """The claims' citations, then every source consulted that none of them cited.

    ``answer`` is the run's prose, ``None`` for a structured answer. The run's bibliography --
    ``trust.sources``, or ``output.sources`` for a run without trust -- is cited without a field, unless the
    prose's source index numbers it and the prose cites that number.
    """
    trust = output.get("trust") or {}
    citations = _claimed(trust)
    markers = _index_markers(answer or "")
    cited_urls = {citation.url for citation in citations}
    for source in [*(trust.get("sources") or []), *(output.get("sources") or [])]:
        url = source.get("url") or ""
        if not url or url in cited_urls:  # a source with no URL names nothing a reader could open
            continue
        cited_urls.add(url)
        marker, passages = markers.get(url, (None, []))
        extra = {**_unmapped(source), **({ANSWER_PASSAGES: passages} if passages else {})}
        citations.append(Citation(url=url, title=source.get("title") or "", field=marker, extra=extra))
    return citations


def _claimed(trust: dict[str, Any]) -> list[Citation]:
    """A trust run's claims, each source once per part of the answer it backs, with its first excerpt."""
    citations: list[Citation] = []
    seen: set[tuple[str, str | None]] = set()
    for claim in trust.get("claims") or []:
        field = claim.get("path")
        if field is None and claim.get("callout") is not None:
            field = str(claim["callout"])
        for cited in claim.get("citations") or []:
            url = cited.get("url") or ""
            if (url, field) in seen:
                continue
            seen.add((url, field))
            excerpts = cited.get("excerpts") or []
            citations.append(
                Citation(
                    url=url,
                    title=cited.get("title") or "",
                    snippet=excerpts[0] if excerpts else "",
                    field=field,
                    extra=_unmapped(cited),
                )
            )
    return citations


def _index_markers(answer: str) -> dict[str, tuple[str, list[str]]]:
    """Each URL the prose's source index numbers -> its marker and the prose's paragraphs citing it.

    A URL the prose never cites by that marker is left out: a list entry alone attributes nothing.
    """
    heading = _INDEX_HEADING.search(answer)
    if heading is None:
        return {}
    body, index = answer[: heading.start()], answer[heading.end() :]
    markers: dict[str, tuple[str, list[str]]] = {}
    for number, url in _INDEX_ENTRY.findall(index):
        marker = f"[{number}]"
        passages = _citing(body, marker)
        if passages and url not in markers:
            markers[url] = (marker, passages)
    return markers


def _citing(body: str, marker: str) -> list[str]:
    """The paragraphs and list items of ``body`` that carry ``marker``, each without its bullet, in order.

    Whole lines, not sentences: a marker closing a paragraph may back all of it, and a split on full stops
    would cut an initial or an abbreviation in two.
    """
    found: list[str] = []
    for line in body.splitlines():
        passage = line.strip().removeprefix("- ").removeprefix("* ").strip()
        if marker in passage and passage not in found:
            found.append(passage)
    return found


def _unmapped(cited: dict[str, Any]) -> dict[str, Any]:
    """Everything a citation carries beyond the normalized fields, including every excerpt."""
    return compact({key: value for key, value in cited.items() if key not in ("url", "title")})
