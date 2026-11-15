"""What the MCP layer keeps of an answer: the answer, and only the sources it cites unless asked."""

from __future__ import annotations

from web_router.constants import MCP_TRUNCATION_MARKER
from web_router.mcp.shaping import render
from web_router.mcp.shaping_answer import shape_answer
from web_router.types import Citation
from web_router.types_answer import AnswerResponse

_PROSE = "alpha beta gamma delta " * 200


def test_a_long_answer_is_cut_and_lists_only_its_cited_sources() -> None:
    uncited = [Citation(url=f"https://read.example/{i}", snippet=_PROSE) for i in range(30)]
    cited = Citation(url="https://cited.example", snippet=_PROSE, field="[31]")
    response = AnswerResponse(provider="perplexity", query="q", answer=_PROSE * 3, citations=[*uncited, cited])

    shaped = shape_answer(response, "answer-0001", budget=4000)

    assert len(render(shaped)) <= 4000
    assert shaped["answer"].endswith(MCP_TRUNCATION_MARKER)
    assert shaped["truncated"] is True
    assert shaped["citations"] == [{"url": "https://cited.example", "marker": "[31]"}], "cited, no snippet"
    assert shaped["uncited_sources"] == 30, "the pages it only read are counted, not listed"


def test_all_sources_lists_the_cited_first_and_snippets_are_opt_in() -> None:
    uncited = [Citation(url=f"https://read.example/{i}", snippet=_PROSE) for i in range(30)]
    cited = Citation(url="https://cited.example", snippet=_PROSE, field="[31]")
    response = AnswerResponse(provider="perplexity", query="q", answer="short", citations=[*uncited, cited])

    shaped = shape_answer(response, "answer-0001", budget=4000, all_sources=True, snippets=True)

    assert shaped["citations"][0]["url"] == "https://cited.example", "a cited source outlives the cut"
    assert shaped["citations"][0]["snippet"].startswith("alpha")
    assert "uncited_sources" not in shaped
    # Snippets are the only text cut, so the answer and its sources are whole: not `truncated`.
    assert shaped["truncated"] is (len(shaped["citations"]) < 31)


def test_an_answer_whose_backend_marks_no_source_lists_them_all() -> None:
    response = AnswerResponse(
        provider="exa", query="q", answer="a", citations=[Citation(url=f"https://e.example/{i}") for i in range(3)]
    )

    shaped = shape_answer(response, "answer-0001", budget=4000)

    assert [row["url"] for row in shaped["citations"]] == [f"https://e.example/{i}" for i in range(3)]
