"""A Nimble research run without trust: its prose's ``[N]`` markers, read against its own ``Source index:``.

The answer below is trimmed from a live low-effort run on 2026-11-12
(``trust_mode: "no_trust"``): Nimble's payload attributed nothing -- ``trust``
was null and ``output.sources`` listed the two pages -- while its prose cited
``[1]`` and ``[2]`` and ended with a numbered source index.
"""

from __future__ import annotations

from typing import Any

from web_router.providers.nimble_citations import ANSWER_PASSAGES, research_citations

SUMMARY = "https://www.nobelprize.org/prizes/physics/2024/summary/"
PRESS = "https://www.nobelprize.org/prizes/physics/2024/press-release/"
UNCITED = "https://example.com/never-cited"

ANSWER = f"""# The 2024 Nobel Prize in Physics

The prize was awarded jointly to **John J. Hopfield** and **Geoffrey Hinton**, sharing it equally [1] [2].

- **John Hopfield** created an associative memory. It stores patterns [2].
- **Geoffrey Hinton** invented the Boltzmann machine.

---

Source index:

- [1] The Nobel Prize in Physics 2024 - NobelPrize.org — {SUMMARY} (news)
- [2] Press release: The Nobel Prize in Physics 2024 - NobelPrize.org — {PRESS} (news)
- [3] Never cited — {UNCITED}
"""

SOURCES = [
    {"url": SUMMARY, "title": "The Nobel Prize in Physics 2024 - NobelPrize.org"},
    {"url": PRESS, "title": "Press release: The Nobel Prize in Physics 2024 - NobelPrize.org"},
    {"url": UNCITED, "title": "Never cited"},
]


def _output(**extra: Any) -> dict[str, Any]:
    return {"type": "text", "trust": None, "sources": SOURCES, **extra}


def test_each_cited_source_carries_its_marker_and_the_passages_citing_it() -> None:
    citations = research_citations(ANSWER, _output())
    assert [(c.url, c.field) for c in citations] == [(SUMMARY, "[1]"), (PRESS, "[2]"), (UNCITED, None)]
    lead = "The prize was awarded jointly to **John J. Hopfield** and **Geoffrey Hinton**, sharing it equally [1] [2]."
    assert citations[0].extra[ANSWER_PASSAGES] == [lead]
    assert citations[1].extra[ANSWER_PASSAGES] == [
        lead,
        "**John Hopfield** created an associative memory. It stores patterns [2].",
    ]
    assert ANSWER_PASSAGES not in citations[2].extra, "listed in the index but never cited in the prose"
    assert all(citation.snippet == "" for citation in citations), "Nimble quoted no page"


def test_a_marker_that_only_appears_in_the_index_attributes_nothing() -> None:
    answer = ANSWER.replace(" [1] [2].", ".").replace(" [2].", ".")
    assert [c.field for c in research_citations(answer, _output())] == [None, None, None]


def test_prose_with_no_source_index_ties_no_source_to_a_marker() -> None:
    answer = ANSWER.split("Source index:")[0]
    assert [c.field for c in research_citations(answer, _output())] == [None, None, None]


def test_a_structured_answer_has_no_markers_to_read() -> None:
    assert [c.field for c in research_citations(None, _output(type="json"))] == [None, None, None]


def test_the_trust_claims_still_come_first_with_their_excerpts() -> None:
    trust = {
        "claims": [{"callout": "[1]", "citations": [{"url": SUMMARY, "title": "t", "excerpts": ["quoted"]}]}],
        "sources": [],
    }
    citations = research_citations(ANSWER, _output(trust=trust))
    assert (citations[0].url, citations[0].field, citations[0].snippet) == (SUMMARY, "[1]", "quoted")
    assert [(c.url, c.field) for c in citations[1:]] == [(PRESS, "[2]"), (UNCITED, None)]
