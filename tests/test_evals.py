"""`evaluate()`: a dataset through several backends, each answer judged, and a score per backend."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import os
import random
import sys
import time
import types
from pathlib import Path
from typing import Any

import httpx
import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.constants import (
    ENV_ANTHROPIC_API_KEY,
    ENV_OPENAI_API_KEY,
    EVALS_ANTHROPIC_JUDGE_MAX_TOKENS,
    EVALS_RESULT_CHARS,
    EVALS_SYNTHESIS_PROMPT,
    EXTRA_EVALS,
    INTEGRATION_RESULT_CHARS,
    INTEGRATION_TRUNCATION_MARKER,
)
from web_router.constants_evals import (
    FRAMES_NAME,
    FRAMES_URL,
    SIMPLEQA_NAME,
    SIMPLEQA_URL,
    SIMPLEQA_VERIFIED_NAME,
    SIMPLEQA_VERIFIED_URL,
)
from web_router.evals import AnthropicJudge, Dataset, EvaluationRow, Judge, OpenAIJudge, Question
from web_router.evals.judge import format_results, grader_prompt, parse_grade
from web_router.exceptions import ConfigurationError, ProviderError, ProviderNotInstalledError
from web_router.integrations._render import render_search
from web_router.registry import register_provider, unregister_provider
from web_router.types import SearchRequest, SearchResponse, SearchResult

ROWS = [
    ("{'topic': 'Science', 'urls': ['https://a.test']}", "Who received the award in 2010?", "Michio Sugeno"),
    ('{"topic": "Art"}', "Which year was the museum founded?", "1929"),
    ("plain text", "What colour is the flag?", "green"),
]


def _csv_text(rows: list[tuple[str, str, str]] = ROWS, header: str = "metadata,problem,answer") -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header.split(","))
    writer.writerows(rows)
    return buffer.getvalue()


def _csv(path: Path, rows: list[tuple[str, str, str]] = ROWS, header: str = "metadata,problem,answer") -> Path:
    target = path / "questions.csv"
    target.write_text(_csv_text(rows, header), encoding="utf-8")
    return target


def _questions(*pairs: tuple[str, str]) -> Dataset:
    return Dataset(name="pairs", questions=[Question(question=q, answer=a) for q, a in pairs])


class FakeJudge(Judge):
    """Answers from the prompt alone: the synthesized answer is looked up per question, the grade is computed.

    Grading reads the question, gold target and predicted answer back out of
    the grader prompt, so the test never reaches past the same interface a
    real model sees.
    """

    name = "fake"

    def __init__(self, answers: dict[str, str] | None = None, *, raise_at: str | None = None) -> None:
        self.answers = answers or {}
        self.raise_at = raise_at
        self.prompts: list[tuple[str, str]] = []

    def _complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        if system:
            if self.raise_at == "synthesis":
                raise RuntimeError("synthesis is down")
            question = user.split("Query: ", 1)[1].split("\n", 1)[0]
            return self.answers.get(question, "I do not know")
        if self.raise_at == "grading":
            raise RuntimeError("grading is down")
        tail = user.rsplit("```\nQuestion: ", 1)[1]
        target = tail.split("Gold target: ", 1)[1].split("\n", 1)[0]
        predicted = tail.split("Predicted answer: ", 1)[1].split("\n```", 1)[0]
        if target in predicted:
            return "A"
        if "do not know" in predicted:
            return "C"
        return "B"

    async def _acomplete(self, system: str, user: str) -> str:
        return self._complete(system, user)


class _Ledger:
    def __init__(self) -> None:
        self.names: list[str] = []


@pytest.fixture
def registered() -> Any:
    ledger = _Ledger()
    yield ledger
    for name in ledger.names:
        unregister_provider(name)


def _member(
    ledger: _Ledger,
    member_name: str,
    *,
    raises: Exception | None = None,
    delay: float = 0.0,
    results: list[SearchResult] | None = None,
) -> type:
    from tests.conftest import FakeProvider

    class Member(FakeProvider):  # type: ignore[misc, valid-type]
        name = member_name

        def _respond(self, request: SearchRequest) -> SearchResponse:
            self.requests.append(request)
            if raises is not None:
                raise raises
            return SearchResponse(provider=self.name, query=request.query, results=results or [])

        def _search(self, request: SearchRequest) -> SearchResponse:
            if delay:
                time.sleep(delay)
            return self._respond(request)

        async def _asearch(self, request: SearchRequest) -> SearchResponse:
            if delay:
                await asyncio.sleep(delay)
            return self._respond(request)

    register_provider(member_name, Member)
    ledger.names.append(member_name)
    return Member


# -- Datasets ----------------------------------------------------------------


def test_a_csv_becomes_questions_with_everything_else_kept_as_metadata(tmp_path: Path) -> None:
    dataset = Dataset.from_csv(_csv(tmp_path))

    assert dataset.name == "questions"
    assert [q.question for q in dataset.questions] == [row[1] for row in ROWS]
    assert [q.answer for q in dataset.questions] == [row[2] for row in ROWS]
    # SimpleQA's metadata is a Python literal; JSON is read too; a plain string stays a string.
    assert dataset.questions[0].metadata == {"metadata": {"topic": "Science", "urls": ["https://a.test"]}}
    assert dataset.questions[1].metadata == {"metadata": {"topic": "Art"}}
    assert dataset.questions[2].metadata == {"metadata": "plain text"}


def test_a_csv_with_its_own_column_names_is_read_by_naming_them(tmp_path: Path) -> None:
    rows = [("x", "What is 1+1?", "2")]
    path = _csv(tmp_path, rows, header="note,query,gold")

    dataset = Dataset.from_csv(path, question_column="query", answer_column="gold", name="arith")

    assert dataset.name == "arith"
    assert dataset.questions[0].question == "What is 1+1?" and dataset.questions[0].answer == "2"
    assert dataset.questions[0].metadata == {"note": "x"}


def test_a_missing_column_names_the_column_and_the_file(tmp_path: Path) -> None:
    path = _csv(tmp_path, [("x", "q", "a")], header="metadata,question,answer")

    with pytest.raises(ValueError, match="'problem'") as caught:
        Dataset.from_csv(path)

    assert str(path) in str(caught.value)


def test_a_short_row_or_blank_cell_is_refused_rather_than_scored_as_gold(tmp_path: Path) -> None:
    # csv.DictReader reads a missing trailing field as None, which str() would
    # turn into the gold answer "None"; a whitespace cell is as meaningless.
    short = tmp_path / "short.csv"
    short.write_text("metadata,problem,answer\nx,Who?\n", encoding="utf-8")
    with pytest.raises(ValueError, match="'answer'") as caught:
        Dataset.from_csv(short)
    assert "row 1" in str(caught.value) and str(short) in str(caught.value)

    with pytest.raises(ValueError, match="'problem'"):
        Dataset.from_csv(_csv(tmp_path, [("x", "   ", "gold")]))


def test_a_sample_is_the_canonical_simple_evals_slice() -> None:
    """`random.Random(seed).sample` on the stdlib RNG is what openai/simple-evals draws with."""
    questions = [Question(question=f"q{i}", answer=f"a{i}") for i in range(20)]
    dataset = Dataset(name="d", questions=questions)

    sampled = dataset.sample(5)

    assert sampled.questions == random.Random(0).sample(questions, 5)
    assert dataset.sample(5, seed=7).questions == random.Random(7).sample(questions, 5)
    assert dataset.sample(5).questions == sampled.questions, "the same seed draws the same slice"
    assert sampled.name == "d"


def test_a_limit_at_or_past_the_size_is_the_whole_set_in_order() -> None:
    dataset = _questions(("q1", "a1"), ("q2", "a2"))

    assert dataset.sample(2).questions == dataset.questions
    assert dataset.sample(50).questions == dataset.questions
    assert dataset.sample(None).questions == dataset.questions
    with pytest.raises(ValueError, match="limit"):
        dataset.sample(0)


def test_a_local_copy_of_simpleqa_is_read_under_its_own_name(tmp_path: Path) -> None:
    dataset = Dataset.simpleqa(path=_csv(tmp_path), limit=2)

    assert dataset.name == SIMPLEQA_NAME
    assert len(dataset) == 2


def test_simpleqa_is_downloaded_from_where_simple_evals_reads_it(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        asked.append((url, kwargs))
        return httpx.Response(200, text=_csv_text(), request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    dataset = Dataset.simpleqa()

    assert asked[0][0] == SIMPLEQA_URL
    assert asked[0][1]["follow_redirects"] is False
    assert dataset.name == SIMPLEQA_NAME and len(dataset) == len(ROWS)
    assert dataset.questions[0].answer == "Michio Sugeno"


FRAMES_TSV = (
    "\tPrompt\tAnswer\twikipedia_link_1\treasoning_types\twiki_links\n"
    "0\tWho is my future wife?\tJane Ballou\thttps://en.wikipedia.org/wiki/A\tMultiple constraints"
    "\t['https://en.wikipedia.org/wiki/A', 'https://en.wikipedia.org/wiki/B']\n"
    "1\tWhere would Bronte tower rank?\t37th\thttps://en.wikipedia.org/wiki/C\tNumerical reasoning"
    "\t['https://en.wikipedia.org/wiki/C']\n"
)


def test_frames_keeps_the_question_the_gold_and_the_upstream_reasoning_types(tmp_path: Path) -> None:
    path = tmp_path / "test.tsv"
    path.write_text(FRAMES_TSV, encoding="utf-8")

    dataset = Dataset.frames(path=path)

    assert dataset.name == FRAMES_NAME
    assert [(q.question, q.answer) for q in dataset.questions] == [
        ("Who is my future wife?", "Jane Ballou"),
        ("Where would Bronte tower rank?", "37th"),
    ]
    first = dataset.questions[0].metadata
    assert first == {
        "reasoning_types": "Multiple constraints",
        "wiki_links": ["https://en.wikipedia.org/wiki/A", "https://en.wikipedia.org/wiki/B"],
    }


def test_frames_is_downloaded_from_hugging_face_following_its_cdn_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        asked.append((url, kwargs))
        return httpx.Response(200, text=FRAMES_TSV, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    dataset = Dataset.frames(limit=1)

    assert asked[0][0] == FRAMES_URL
    assert asked[0][1]["follow_redirects"] is True
    assert len(dataset) == 1


SIMPLEQA_VERIFIED_CSV = (
    "original_index,problem,answer,topic,answer_type,multi_step,requires_reasoning,urls\n"
    '5,How much was the surgeon held responsible for?,"120,000 euros",Politics,Number,True,False,https://a.example\n'
    "9,Who painted it?,Ada Lovelace,Art,Person,False,False,https://b.example\n"
)


def test_simpleqa_verified_is_read_at_its_pinned_commit_with_its_upstream_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        asked.append((url, kwargs))
        return httpx.Response(200, text=SIMPLEQA_VERIFIED_CSV, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    dataset = Dataset.simpleqa_verified()

    assert asked[0][0] == SIMPLEQA_VERIFIED_URL and "/resolve/main/" not in SIMPLEQA_VERIFIED_URL
    assert asked[0][1]["follow_redirects"] is True
    assert dataset.name == SIMPLEQA_VERIFIED_NAME
    assert [(q.question, q.answer) for q in dataset.questions] == [
        ("How much was the surgeon held responsible for?", "120,000 euros"),
        ("Who painted it?", "Ada Lovelace"),
    ]
    assert dataset.questions[0].metadata["topic"] == "Politics"
    assert len(Dataset.simpleqa_verified(limit=1)) == 1


# -- The judge ---------------------------------------------------------------


def _sealqa_parquet(path: Path) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "question": ["Who holds the record?", "Which year?"],
            "answer": ["Serban Ghenea", "2024"],
            "urls": ["['https://en.wikipedia.org/wiki/x']", "['https://example.com']"],
            "freshness": ["fast-changing", "never-changing"],
            "question_types": ["['entity/event disambiguation']", "['temporal']"],
            "effective_year": ["2024", "2023"],
            "search_results": ["conflicting", "unhelpful"],
            "topic": ["Entertainment", "Science"],
            "canary": ["sealqa:canary", "sealqa:canary"],
        }
    )
    pq.write_table(table, path)
    return path


def test_sealqa_keeps_its_labels_drops_its_canary_and_names_its_split(tmp_path: Path) -> None:
    dataset = Dataset.sealqa("seal-hard", path=_sealqa_parquet(tmp_path / "seal-hard.parquet"))

    assert dataset.name == "sealqa-seal-hard" and len(dataset) == 2
    first = dataset.questions[0]
    assert (first.question, first.answer) == ("Who holds the record?", "Serban Ghenea")
    assert first.metadata["freshness"] == "fast-changing" and first.metadata["search_results"] == "conflicting"
    assert first.metadata["question_types"] == ["entity/event disambiguation"]
    assert "canary" not in first.metadata, "the canary marks copies of the set; it stays out of every result"


def test_sealqa_is_read_at_its_pinned_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = _sealqa_parquet(tmp_path / "seal-0.parquet").read_bytes()
    seen: dict[str, Any] = {}

    def get(url: str, **kwargs: Any) -> httpx.Response:
        seen.update(url=url, **kwargs)
        return httpx.Response(200, content=data, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    dataset = Dataset.sealqa()

    assert seen["url"].endswith("/0c298f519c871579c75920189b3858da29fe2c81/seal-0.parquet")
    assert dataset.name == "sealqa-seal-0" and len(dataset) == 2


def test_sealqa_refuses_a_split_it_does_not_have() -> None:
    with pytest.raises(ValueError, match="longseal"):
        Dataset.sealqa("longseal")


def test_sealqa_without_pyarrow_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    with pytest.raises(ConfigurationError, match=r"--extra evals"):
        Dataset.sealqa()


def test_a_sample_keeps_what_the_set_withholds(tmp_path: Path) -> None:
    dataset = Dataset.sealqa(path=_sealqa_parquet(tmp_path / "seal-0.parquet")).sample(1, seed=3)

    assert len(dataset) == 1 and dataset.withholds("https://huggingface.co/datasets/vtllms/sealqa")


@pytest.mark.parametrize(
    ("url", "withheld"),
    [
        ("https://huggingface.co/datasets/vtllms/sealqa", True),
        ("https://www.arxiv.org/abs/2506.01062", True),
        ("https://arxiv.org/pdf/2506.01062v2", True),
        ("https://www.alphaxiv.org/abs/2506.01062", True),
        ("https://huggingface.co/papers/2506.01062", True),
        ("https://github.com/someone/SealQA-mirror/blob/main/data.json", True),
        ("https://x.com/Se%61lQA", True),
        ("https://x.com/Se%2561lQA", True),
        ("huggingface.co/datasets/vtllms/sealqa", True),
        ("https://colab.research.google.com/drive/1fJunutgxNaiU6-Met_wzyEdG-tXQLfrM", True),
        ("https://arxiv.org/abs/1706.03762", False),
        ("https://huggingface.co/datasets/other/ds", False),
        ("https://en.wikipedia.org/wiki/Grammy_Award", False),
        ("http://[::1/unclosed", True),
        ("https://x.com/%" + "25" * 8 + "41", True),
        ("https://x.com/%" + "25" * 2 + "41", False),
    ],
)
def test_sealqa_withholds_its_own_pages_and_copies_but_not_their_hosts(
    tmp_path: Path, url: str, withheld: bool
) -> None:
    dataset = Dataset.sealqa(path=_sealqa_parquet(tmp_path / "seal-0.parquet"))

    assert dataset.withholds(url) is withheld


def test_a_plain_set_withholds_nothing_even_an_unreadable_url() -> None:
    assert not _questions(("Who?", "Alice")).withholds("https://huggingface.co/anything")
    assert not _questions(("Who?", "Alice")).withholds("https://huggingface.co/datasets/vtllms/sealqa")
    assert not _questions(("Who?", "Alice")).withholds("http://[::1/unclosed")


def test_the_grader_prompt_is_the_vendored_one_with_its_three_slots() -> None:
    vendored = Path("src/web_router/evals/prompts/simpleqa_grader.txt").read_text(encoding="utf-8")

    prompt = grader_prompt()
    filled = prompt.format(question="Who?", target="Alice", predicted_answer="Alice Smith")

    assert prompt == vendored.removesuffix("\n")
    assert "Question: Who?\nGold target: Alice\nPredicted answer: Alice Smith" in filled
    assert filled.endswith('Just return the letters "A", "B", or "C", with no text around it.')


def test_a_grade_is_read_from_the_letter_the_reply_names() -> None:
    assert parse_grade("A") == "correct"
    assert parse_grade("B") == "incorrect"
    assert parse_grade("C") == "not_attempted"
    assert parse_grade("The answer is B, clearly.") == "incorrect"
    assert parse_grade("") == "not_attempted", "a reply naming no grade is read as not attempted, as upstream does"
    assert parse_grade("I cannot grade this.") == "not_attempted"


def test_results_reach_the_synthesizer_in_rank_order_as_an_agent_reads_them() -> None:
    results = [
        SearchResult(url="https://one.test", title="One", description="page head", content="body one"),
        SearchResult(url="https://two.test", title="Two", content="x" * (EVALS_RESULT_CHARS + 50)),
        SearchResult(url="https://three.test", title="Three", description="snippet only"),
    ]

    first, second, third = format_results(results).split("\n\n")

    assert first == "[One](https://one.test)\n body one", "the content, not a description cut from its head"
    assert second.startswith("[Two](https://two.test)\n ")
    body = second.split("\n ", 1)[1]
    assert body == "x" * EVALS_RESULT_CHARS + INTEGRATION_TRUNCATION_MARKER, "cut, not dropped, and marked as cut"
    assert third == "[Three](https://three.test)\n snippet only", "a result with no content falls back"


def test_the_synthesizer_reads_what_the_integrations_hand_an_agent() -> None:
    long = SearchResult(url="https://long.test", title="Long", content="word " * 1000)
    rendered = json.loads(render_search(SearchResponse(provider="p", query="q", results=[long])))

    assert EVALS_RESULT_CHARS == INTEGRATION_RESULT_CHARS
    assert format_results([long]).split("\n ", 1)[1] == rendered["results"][0]["content"]


def test_the_built_in_judge_without_the_sdk_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)
    judge = OpenAIJudge(api_key="k")

    with pytest.raises(ProviderNotInstalledError) as caught:
        judge.check()

    assert caught.value.extra == EXTRA_EVALS
    with pytest.raises(ProviderNotInstalledError):
        judge.grade("q", "t", "p")


def test_the_built_in_judge_without_a_key_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_OPENAI_API_KEY, raising=False)
    monkeypatch.setitem(sys.modules, "openai", types.ModuleType("openai"))
    judge = OpenAIJudge()

    with pytest.raises(ConfigurationError, match=ENV_OPENAI_API_KEY):
        judge.check()


def test_the_built_in_judge_omits_temperature_only_for_models_that_refuse_one() -> None:
    assert OpenAIJudge(api_key="k").temperature == 0.0
    assert OpenAIJudge("gpt-5-mini", api_key="k").temperature is None
    assert OpenAIJudge("gpt-4o", api_key="k", temperature=0.3).temperature == 0.3
    assert OpenAIJudge(api_key="k").label == "openai:gpt-4o"
    assert OpenAIJudge("gpt-4.1", api_key="k").label == "openai:gpt-4.1"


class _FakeCompletions:
    def __init__(self, calls: list[dict[str, Any]], reply: str, is_async: bool) -> None:
        self.calls, self.reply, self.is_async = calls, reply, is_async

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        message = types.SimpleNamespace(content=self.reply)
        response = types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])
        if self.is_async:

            async def answer() -> Any:
                return response

            return answer()
        return response


def _fake_openai(reply: str) -> tuple[types.ModuleType, dict[str, Any]]:
    """A stand-in for the SDK: records every request, answers ``reply``, and notes which client was closed."""
    seen: dict[str, Any] = {"calls": [], "built": [], "closed": []}

    def make(is_async: bool) -> type:
        class Client:
            def __init__(self, **kwargs: Any) -> None:
                seen["built"].append(kwargs)
                self.chat = types.SimpleNamespace(completions=_FakeCompletions(seen["calls"], reply, is_async))

            def close(self) -> Any:
                seen["closed"].append(self)
                if is_async:

                    async def done() -> None:
                        return None

                    return done()
                return None

        return Client

    module = types.ModuleType("openai")
    module.OpenAI = make(False)  # type: ignore[attr-defined]
    module.AsyncOpenAI = make(True)  # type: ignore[attr-defined]
    return module, seen


def test_the_built_in_judge_sends_the_prompts_and_reads_the_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    module, seen = _fake_openai("B")
    monkeypatch.setitem(sys.modules, "openai", module)
    judge = OpenAIJudge("gpt-4o", api_key="secret", base_url="https://llm.test/v1", timeout=9.0)
    results = [SearchResult(url="https://a.test", title="A", description="snippet")]

    answer = judge.synthesize("Who?", results)
    grade = judge.grade("Who?", "Alice", answer)
    judge.close()

    assert answer == "B" and grade == "incorrect"
    assert seen["built"] == [{"api_key": "secret", "base_url": "https://llm.test/v1", "timeout": 9.0}]
    synthesis, grading = seen["calls"]
    assert synthesis["model"] == "gpt-4o" and synthesis["temperature"] == 0.0
    assert synthesis["messages"][0] == {"role": "system", "content": EVALS_SYNTHESIS_PROMPT}
    assert synthesis["messages"][1]["role"] == "user"
    assert (
        "Query: Who?" in synthesis["messages"][1]["content"]
        and "[A](https://a.test)" in synthesis["messages"][1]["content"]
    )
    assert [m["role"] for m in grading["messages"]] == ["user"], "the grader prompt is the whole message"
    assert "Gold target: Alice\nPredicted answer: B" in grading["messages"][0]["content"]
    assert len(seen["closed"]) == 1, "close() shuts the one client that was built, and only it"
    judge.close()
    assert len(seen["closed"]) == 1, "closing again is a no-op"


async def test_the_built_in_judge_works_the_same_way_async(monkeypatch: pytest.MonkeyPatch) -> None:
    module, seen = _fake_openai("A")
    monkeypatch.setitem(sys.modules, "openai", module)
    judge = OpenAIJudge("gpt-5", api_key="secret")

    grade = await judge.agrade("Who?", "Alice", "Alice")
    await judge.aclose()

    assert grade == "correct"
    assert "temperature" not in seen["calls"][0], "gpt-5 rejects any temperature but its own"
    assert len(seen["built"]) == 1 and len(seen["closed"]) == 1


def test_the_anthropic_judge_without_the_sdk_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", None)
    judge = AnthropicJudge(api_key="k")

    with pytest.raises(ProviderNotInstalledError) as caught:
        judge.check()

    assert caught.value.extra == EXTRA_EVALS
    with pytest.raises(ProviderNotInstalledError):
        judge.grade("q", "t", "p")


def test_the_anthropic_judge_without_a_key_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_ANTHROPIC_API_KEY, raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", types.ModuleType("anthropic"))
    judge = AnthropicJudge()

    with pytest.raises(ConfigurationError, match=ENV_ANTHROPIC_API_KEY):
        judge.check()


def test_the_anthropic_judge_is_labelled_by_its_model_and_keeps_its_key_out_of_repr() -> None:
    assert AnthropicJudge(api_key="secret").label == "anthropic:claude-sonnet-5-5"
    assert AnthropicJudge("claude-opus-5-5", api_key="secret").label == "anthropic:claude-opus-5-5"
    assert "secret" not in repr(AnthropicJudge(api_key="secret"))


class _FakeMessages:
    def __init__(self, calls: list[dict[str, Any]], reply: str, stop_reason: str, is_async: bool) -> None:
        self.calls, self.reply, self.stop_reason, self.is_async = calls, reply, stop_reason, is_async

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        content = [
            types.SimpleNamespace(type="thinking", thinking=""),
            types.SimpleNamespace(type="text", text=self.reply),
        ]
        response = types.SimpleNamespace(stop_reason=self.stop_reason, content=content)
        if self.is_async:

            async def answer() -> Any:
                return response

            return answer()
        return response


def _fake_anthropic(reply: str, stop_reason: str = "end_turn") -> tuple[types.ModuleType, dict[str, Any]]:
    """The Anthropic SDK's stand-in, as ``_fake_openai`` is OpenAI's: a thinking block precedes the text."""
    seen: dict[str, Any] = {"calls": [], "built": [], "closed": []}

    def make(is_async: bool) -> type:
        class Client:
            def __init__(self, **kwargs: Any) -> None:
                seen["built"].append(kwargs)
                self.messages = _FakeMessages(seen["calls"], reply, stop_reason, is_async)

            def close(self) -> Any:
                seen["closed"].append(self)
                if is_async:

                    async def done() -> None:
                        return None

                    return done()
                return None

        return Client

    module = types.ModuleType("anthropic")
    module.Anthropic = make(False)  # type: ignore[attr-defined]
    module.AsyncAnthropic = make(True)  # type: ignore[attr-defined]
    return module, seen


def test_the_anthropic_judge_sends_the_prompts_and_reads_the_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    module, seen = _fake_anthropic("B")
    monkeypatch.setitem(sys.modules, "anthropic", module)
    judge = AnthropicJudge(api_key="secret", base_url="https://llm.test", max_tokens=512, timeout=9.0)
    results = [SearchResult(url="https://a.test", title="A", description="snippet")]

    answer = judge.synthesize("Who?", results)
    grade = judge.grade("Who?", "Alice", answer)
    judge.close()

    assert answer == "B" and grade == "incorrect", "the answer is the text blocks alone, thinking left out"
    assert seen["built"] == [{"api_key": "secret", "base_url": "https://llm.test", "timeout": 9.0}]
    synthesis, grading = seen["calls"]
    assert synthesis["model"] == "claude-sonnet-5-5" and synthesis["max_tokens"] == 512
    assert synthesis["system"] == EVALS_SYNTHESIS_PROMPT
    [message] = synthesis["messages"]
    assert (
        message["role"] == "user"
        and "Query: Who?" in message["content"]
        and "[A](https://a.test)" in message["content"]
    )
    assert "system" not in grading, "the grader prompt is the whole message"
    assert [m["role"] for m in grading["messages"]] == ["user"]
    assert grading["messages"][0]["content"] == grader_prompt().format(
        question="Who?", target="Alice", predicted_answer="B"
    )
    assert all("temperature" not in call for call in seen["calls"]), (
        "Claude models refuse any temperature but their own"
    )
    assert len(seen["closed"]) == 1, "close() shuts the one client that was built, and only it"
    judge.close()
    assert len(seen["closed"]) == 1, "closing again is a no-op"


async def test_the_anthropic_judge_works_the_same_way_async(monkeypatch: pytest.MonkeyPatch) -> None:
    module, seen = _fake_anthropic("A")
    monkeypatch.setitem(sys.modules, "anthropic", module)
    judge = AnthropicJudge(api_key="secret")

    grade = await judge.agrade("Who?", "Alice", "Alice")
    await judge.aclose()

    assert grade == "correct"
    assert seen["calls"][0]["max_tokens"] == EVALS_ANTHROPIC_JUDGE_MAX_TOKENS
    assert len(seen["built"]) == 1 and len(seen["closed"]) == 1


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_an_anthropic_reply_cut_short_raises_rather_than_being_graded_as_not_attempted(
    monkeypatch: pytest.MonkeyPatch, stop_reason: str
) -> None:
    module, _ = _fake_anthropic("", stop_reason=stop_reason)
    monkeypatch.setitem(sys.modules, "anthropic", module)
    judge = AnthropicJudge(api_key="secret")

    with pytest.raises(ProviderError, match=stop_reason):
        judge.grade("Who?", "Alice", "Alice")


# -- evaluate() --------------------------------------------------------------


def test_every_provider_answers_every_question_and_is_scored_in_the_order_rows_arrived(registered: _Ledger) -> None:
    _member(registered, "first")
    _member(registered, "second")
    dataset = _questions(("Who?", "Alice"), ("When?", "1999"), ("Where?", "Paris"))
    judge = FakeJudge({"Who?": "It was Alice.", "When?": "In 1998.", "Where?": "I do not know"})

    with WebRouter("first") as router:
        report = router.evaluate(dataset, providers=["second", "first"], judge=judge)

    assert len(report) == 6
    assert [score.provider for score in report.scores] == ["second", "first"]
    assert report.dataset == "pairs" and report.judge == judge.label
    for provider in ("first", "second"):
        score = report[provider]
        assert (score.questions, score.served, score.failed) == (3, 3, 0)
        assert (score.correct, score.incorrect, score.not_attempted) == (1, 1, 1)
        assert score.accuracy == pytest.approx(1 / 3), "a served question the answer missed counts as a miss"
        assert score.accuracy_when_attempted == pytest.approx(1 / 2), "the simple-evals figure leaves it out"
        assert score.failure_rate == 0.0
    with pytest.raises(KeyError):
        report["nobody"]
    graded = {(row.provider, row.question): row.grade for row in report.rows}
    assert graded[("first", "Who?")] == "correct" and graded[("second", "When?")] == "incorrect"
    assert graded[("first", "Where?")] == "not_attempted"
    assert next(row for row in report.rows if row.question == "Who?").answer == "It was Alice."


def test_the_judge_sees_the_backends_results_and_the_datasets_gold(registered: _Ledger) -> None:
    results = [SearchResult(url="https://a.test", title="A", description="Alice won it")]
    _member(registered, "first", results=results)
    judge = FakeJudge({"Who?": "Alice"})

    with WebRouter("first") as router:
        router.evaluate(_questions(("Who?", "Alice")), providers=["first"], judge=judge)

    synthesis, grading = judge.prompts
    assert synthesis[0] == EVALS_SYNTHESIS_PROMPT
    assert "Query: Who?" in synthesis[1] and "[A](https://a.test)\n Alice won it" in synthesis[1]
    assert grading[0] == "" and "Gold target: Alice\nPredicted answer: Alice" in grading[1]


def test_results_from_pages_holding_the_answer_key_never_reach_the_judge(registered: _Ledger) -> None:
    results = [
        SearchResult(url="https://huggingface.co/datasets/vtllms/sealqa", title="Key", content="Alice"),
        SearchResult(url="https://a.test", title="A", content="Alice won it"),
    ]
    _member(registered, "first", results=results)
    judge = FakeJudge({"Who?": "Alice"})
    dataset = _questions(("Who?", "Alice")).model_copy(update={"withheld_markers": ("huggingface.co",)})

    with WebRouter("first") as router:
        report = router.evaluate(dataset, providers=["first"], judge=judge)

    synthesis = judge.prompts[0][1]
    assert "https://a.test" in synthesis and "huggingface.co" not in synthesis
    assert report.rows[0].withheld == 1


def test_a_backend_that_fails_is_left_out_of_accuracy_and_counted_as_a_failure(registered: _Ledger) -> None:
    _member(registered, "broken", raises=ProviderError("broken", "upstream 500"))
    _member(registered, "fine")
    dataset = _questions(("Who?", "Alice"), ("When?", "1999"))
    judge = FakeJudge({"Who?": "Alice", "When?": "1999"})

    with WebRouter("fine") as router:
        report = router.evaluate(dataset, providers=["broken", "fine"], judge=judge)

    broken = report["broken"]
    assert (broken.questions, broken.served, broken.failed) == (2, 0, 2)
    assert broken.accuracy is None and broken.accuracy_when_attempted is None
    assert broken.failure_rate == 1.0 and broken.latency_p50 is None
    rows = [row for row in report.rows if row.provider == "broken"]
    assert all(row.failed_at == "search" and isinstance(row.error, ProviderError) for row in rows)
    assert all("upstream 500" in str(row.error) and row.grade is None and not row.served for row in rows)
    assert report["fine"].accuracy == 1.0
    assert all("Query: Who?" not in p[1] or "broken" not in p[1] for p in judge.prompts)
    assert len(judge.prompts) == 4, "the judge was never asked about a row the backend did not serve"


def test_a_judge_that_fails_marks_the_row_where_it_failed_under_its_own_name(registered: _Ledger) -> None:
    _member(registered, "first")
    dataset = _questions(("Who?", "Alice"))

    with WebRouter("first") as router:
        at_synthesis = router.evaluate(dataset, providers=["first"], judge=FakeJudge(raise_at="synthesis"))
        at_grading = router.evaluate(
            dataset, providers=["first"], judge=FakeJudge({"Who?": "Alice"}, raise_at="grading")
        )

    row = at_synthesis.rows[0]
    assert row.failed_at == "synthesis" and row.answer is None and row.grade is None
    assert isinstance(row.error, ProviderError) and row.error.provider == "fake"
    assert isinstance(row.error.original, RuntimeError) and "synthesis is down" in str(row.error)
    assert at_synthesis["first"].failed == 1 and at_synthesis["first"].accuracy is None

    row = at_grading.rows[0]
    assert row.failed_at == "grading" and row.answer == "Alice", "what was synthesized is kept"
    assert row.grade is None and "grading is down" in str(row.error)
    assert at_grading["first"].failure_rate == 1.0
    assert at_grading.model_dump()["rows"][0]["error"] == str(row.error)


def test_latency_percentiles_are_measured_values_at_the_nearest_rank(registered: _Ledger) -> None:
    _member(registered, "first")
    dataset = _questions(*((f"q{i}", "a") for i in range(5)))

    with WebRouter("first") as router:
        report = router.evaluate(dataset, providers=["first"], judge=FakeJudge())

    latencies = sorted(row.latency for row in report.rows)
    score = report["first"]
    assert score.latency_p50 == latencies[math.ceil(0.5 * 5) - 1]
    assert score.latency_p95 == latencies[math.ceil(0.95 * 5) - 1]
    assert score.latency_p50 in latencies and score.latency_p95 in latencies


def test_an_empty_dataset_is_refused_before_any_search(registered: _Ledger) -> None:
    member_cls = _member(registered, "first")

    with WebRouter("first") as router:
        with pytest.raises(ValueError, match="at least one question"):
            router.evaluate(Dataset(name="empty"), providers=["first"], judge=FakeJudge())
        assert router.get_provider("first").requests == []
    assert isinstance(router.get_provider("first"), member_cls)


def test_the_default_judge_without_a_key_fails_before_any_search_is_paid_for(
    registered: _Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_OPENAI_API_KEY, raising=False)
    monkeypatch.setitem(sys.modules, "openai", types.ModuleType("openai"))
    _member(registered, "first")

    with WebRouter("first") as router:
        with pytest.raises(ConfigurationError, match=ENV_OPENAI_API_KEY):
            router.evaluate(_questions(("Who?", "Alice")), providers=["first"])
        assert router.get_provider("first").requests == []


def test_a_judge_passed_in_without_a_key_fails_before_any_search_is_paid_for(
    registered: _Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_ANTHROPIC_API_KEY, raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", types.ModuleType("anthropic"))
    _member(registered, "first")

    with WebRouter("first") as router:
        with pytest.raises(ConfigurationError, match=ENV_ANTHROPIC_API_KEY):
            router.evaluate(_questions(("Who?", "Alice")), providers=["first"], judge=AnthropicJudge())
        assert router.get_provider("first").requests == []


def test_a_judge_built_by_default_is_closed_and_one_passed_in_is_not(
    registered: _Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, seen = _fake_openai("A")
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv(ENV_OPENAI_API_KEY, "secret")
    _member(registered, "first")
    dataset = _questions(("Who?", "Alice"))

    with WebRouter("first") as router:
        report = router.evaluate(dataset, providers=["first"])
        assert report.judge == "openai:gpt-4o" and report["first"].accuracy == 1.0
        assert len(seen["closed"]) == 1, "the judge evaluate() built is closed by evaluate()"

        theirs = OpenAIJudge(api_key="secret")
        router.evaluate(dataset, providers=["first"], judge=theirs)
        assert len(seen["closed"]) == 1, "a judge the caller passed in is the caller's to close"
        theirs.close()
        assert len(seen["closed"]) == 2


def test_the_search_options_reach_every_backend_unchanged(registered: _Ledger) -> None:
    _member(registered, "first")
    _member(registered, "second")

    with WebRouter("first") as router:
        router.evaluate(
            _questions(("Who?", "Alice")),
            providers=["first", "second"],
            judge=FakeJudge(),
            max_results=4,
            search_depth="deep",
        )
        requests = [router.get_provider(name).requests for name in ("first", "second")]

    for sent in requests:
        assert len(sent) == 1
        assert sent[0].query == "Who?" and sent[0].max_results == 4 and sent[0].search_depth == "deep"
    assert requests[0] == requests[1]


async def test_the_async_router_evaluates_the_same_way(registered: _Ledger) -> None:
    _member(registered, "broken", raises=ProviderError("broken", "down"))
    _member(registered, "fine")
    dataset = _questions(("Who?", "Alice"), ("When?", "1999"))
    judge = FakeJudge({"Who?": "Alice", "When?": "2001"})

    async with AsyncWebRouter("fine") as router:
        report = await router.evaluate(dataset, providers=["fine", "broken"], judge=judge, max_results=2)
        sent = router.get_provider("fine").requests

    assert [score.provider for score in report.scores] == ["fine", "broken"]
    fine = report["fine"]
    assert (fine.correct, fine.incorrect, fine.accuracy) == (1, 1, 0.5)
    assert report["broken"].failed == 2 and report["broken"].accuracy is None
    assert all(request.max_results == 2 for request in sent)
    assert report.judge == judge.label


async def test_the_rows_of_one_question_are_judged_at_once(registered: _Ledger) -> None:
    _member(registered, "one", delay=0.2)
    _member(registered, "two", delay=0.2)

    started = time.perf_counter()
    async with AsyncWebRouter("one") as router:
        report = await router.evaluate(_questions(("Who?", "Alice")), providers=["one", "two"], judge=FakeJudge())
    elapsed = time.perf_counter() - started

    assert len(report) == 2 and all(row.served for row in report.rows)
    assert elapsed < 0.35, "two 0.2s backends on one question took as long as one"


async def test_concurrency_bounds_how_many_questions_are_in_flight(registered: _Ledger) -> None:
    _member(registered, "one", delay=0.15)
    dataset = _questions(("q1", "a"), ("q2", "a"))

    async with AsyncWebRouter("one") as router:
        started = time.perf_counter()
        await router.evaluate(dataset, providers=["one"], judge=FakeJudge(), concurrency=1)
        serial = time.perf_counter() - started
        started = time.perf_counter()
        await router.evaluate(dataset, providers=["one"], judge=FakeJudge(), concurrency=2)
        parallel = time.perf_counter() - started

    assert serial >= 0.3, "concurrency=1 runs the questions one after another"
    assert parallel < 0.28, "concurrency=2 runs both at once"


def test_an_evaluation_row_carries_exactly_one_outcome() -> None:
    from pydantic import ValidationError

    base: dict[str, Any] = {"provider": "p", "question": "q", "target": "t", "latency": 0.0}
    assert EvaluationRow(**base, grade="correct").served
    assert not EvaluationRow(**base, error=ProviderError("p", "x"), failed_at="search").served
    with pytest.raises(ValidationError):
        EvaluationRow(**base)
    with pytest.raises(ValidationError):
        EvaluationRow(**base, error=ProviderError("p", "x"))
    with pytest.raises(ValidationError):
        EvaluationRow(**base, grade="correct", failed_at="search")


def test_the_sync_router_runs_questions_on_threads_up_to_concurrency(registered: _Ledger) -> None:
    _member(registered, "one", delay=0.15)
    dataset = _questions(("q1", "a"), ("q2", "a"), ("q3", "a"))

    started = time.perf_counter()
    with WebRouter("one") as router:
        report = router.evaluate(dataset, providers=["one"], judge=FakeJudge(), concurrency=3)
    elapsed = time.perf_counter() - started

    assert report["one"].questions == 3
    assert elapsed < 0.4, "three 0.15s questions on three threads took as long as one"


def test_a_judge_shared_across_threads_builds_one_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor

    module, seen = _fake_openai("A")
    monkeypatch.setitem(sys.modules, "openai", module)
    judge = OpenAIJudge(api_key="secret")
    results = [SearchResult(url="https://a.test", title="A", description="snippet")]

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: judge.synthesize("Who?", results), range(4)))
    judge.close()

    assert len(seen["built"]) == 1, "racing first calls each built a client close() never saw"
    assert len(seen["closed"]) == 1


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv(ENV_ANTHROPIC_API_KEY), reason=f"{ENV_ANTHROPIC_API_KEY} not set")
def test_live_anthropic_judge_answers_and_grades_with_its_default_model() -> None:
    judge = AnthropicJudge()
    results = [
        SearchResult(
            url="https://en.wikipedia.org/wiki/Paris", title="Paris", description="Paris is the capital of France."
        )
    ]

    try:
        answer = judge.synthesize("What is the capital of France?", results)
        grade = judge.grade("What is the capital of France?", "Paris", answer)
    finally:
        judge.close()

    assert "Paris" in answer
    assert grade == "correct"


def test_a_csv_is_identified_by_its_bytes_and_columns(tmp_path: Path) -> None:
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    first.write_text("q,a\nWho?,Alice\n", encoding="utf-8")
    second.write_text("q,a\nWho?,Bob\n", encoding="utf-8")
    one = Dataset.csv_identity(first, "q", "a")

    assert one == Dataset.csv_identity(first, "q", "a") and one != Dataset.csv_identity(second, "q", "a")
    assert one["question_column"] == "q" and len(one["dataset_sha256"]) == 64
