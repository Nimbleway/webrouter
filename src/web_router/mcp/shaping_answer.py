"""An ``answer`` result: the backend's answer first, then the sources it cites.

Kept apart from :mod:`web_router.mcp.shaping`, so that file stops growing. A
backend returns every page it read, often ten with a long quoted passage each,
while the answer cites a few: by default only the cited ones are listed, by
marker, and their passages are left behind the handle with the rest.
"""

from __future__ import annotations

from typing import Any

from web_router.mcp.shaping import _cut_answer, _fit, compact
from web_router.types import Citation
from web_router.types_answer import AnswerResponse


def shape_answer(
    response: AnswerResponse, handle: str, *, budget: int, all_sources: bool = False, snippets: bool = False
) -> dict[str, Any]:
    """A written answer and its sources, cut to fit -- the answer first, with its own share of the budget.

    A source is cited when it carries a marker (``field``, shown as ``marker``).
    Unless ``all_sources``, the uncited ones are counted, not listed -- or, when
    the backend marks none, every source is listed, since then none can be told
    apart. With ``all_sources`` the cited come first, so a cut, which drops from
    the end, drops an uncited page before a cited one. ``snippets`` adds each
    source's quoted passage.
    """
    answer = _cut_answer(response.answer, budget)
    cited = [citation for citation in response.citations if citation.field is not None]
    if all_sources or not cited:
        shown = sorted(response.citations, key=lambda citation: citation.field is None)
    else:
        shown = cited
    rows = [_row(citation, snippets=snippets) for citation in shown]
    payload = compact(
        {
            "provider": response.provider,
            "query": response.query,
            "outcome": response.outcome,
            "answer": answer,
            "citations": rows,
            "uncited_sources": len(response.citations) - len(shown) or None,
        }
    )
    shortened = answer != response.answer
    return _fit(payload, rows, ("snippet",), handle=handle, budget=budget, shortened=shortened, main=False)


def _row(citation: Citation, *, snippets: bool) -> dict[str, Any]:
    row = {"url": citation.url, "title": citation.title, "marker": citation.field}
    return compact({**row, "snippet": citation.snippet} if snippets else row)
