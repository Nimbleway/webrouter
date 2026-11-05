"""The tool and effort set's questions and paths: which questions it asks, and every call it makes on each.

Kept apart from ``tools_effort.py`` so ``frozen.py`` can name the rows a
finished run must hold without importing the runner. Imported as a sibling, by
name, as ``_common`` is.

Experimental until 1.0: the set's size and paths may change.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from _frozen_checks import DEFAULT_MODE, question_id

SET_NAME = "tools-effort-60"
QUESTIONS = "tools-effort"
"""What the frozen manifest names as the set's questions: several public sets, so ``join`` reads its ``sample``."""
DATASET = "SimpleQA, SealQA Seal-Hard and FRAMES, sampled"

EASY, HARD, SYNTHESIS, URL_GIVEN = "easy", "hard", "synthesis", "url-given"
SIMPLEQA, SEALQA_HARD, FRAMES = "simpleqa", "sealqa-hard", "frames"

SIMPLEQA_SAMPLE = (300, 0)
"""The sample behind the published SimpleQA search and answer sets: its first questions join their rows."""
SEALQA_SEED = 0
FRAMES_SAMPLE = (300, 2)
"""The agent benchmark's FRAMES sample, so its rows join too."""
SIZES = {EASY: 10, HARD: 20, SYNTHESIS: 20}
URL_GIVEN_SIZES = {SIMPLEQA: 5, FRAMES: 5}
"""Questions given with the pages to read: SimpleQA's first source URL, FRAMES' Wikipedia links."""
MAX_GIVEN_URLS = 5

SEARCH_EXTRACT, EXTRACT, ANSWER, RESEARCH = "search+extract", "extract", "answer", "research"
PATHS = (SEARCH_EXTRACT, EXTRACT, ANSWER, RESEARCH)
"""Each path is a frozen row's ``method``."""

SEARCH_PROVIDERS = ("nimble", "perplexity", "linkup")
READER = "nimble"
"""One extractor reads every page, so only the search varies between search+extract rows."""
MAX_RESULTS = 5
PAGES_READ = 3
TOP_MODE = f"top{PAGES_READ}"
ANSWER_PROVIDERS = ("perplexity", "you", "parallel", "exa", "linkup", "brave")
REUSED_ANSWER_SET = "answer-simpleqa-300"
REUSED_ANSWERS = {EASY: ("perplexity", "you", "parallel", "exa", "linkup")}
"""Answer rows not asked again: the easy questions already have them in the frozen answer set, by the same judge."""
RESEARCH_EFFORTS = (
    ("nimble", ("low", "medium", "high")),
    ("parallel", ("low", "medium", "high", "max")),
    ("linkup", ("low",)),
    ("perplexity", ("low", "medium")),
)
"""Nimble's ``max`` has no published price; Linkup and Perplexity stop where the approved budget does."""


@dataclass(frozen=True)
class Item:
    """One question of the set: where it comes from, and the pages it is given, if any."""

    group: str
    source: str
    index: int
    """Its row in the public file, in upstream order: how ``join`` finds it again."""
    question: str = ""
    answer: str = ""
    urls: tuple[str, ...] = ()
    qid: str = ""

    @classmethod
    def make(cls, group: str, source: str, index: int, question: Any, urls: Sequence[str] = ()) -> Item:
        return cls(
            group=group,
            source=source,
            index=index,
            question=question.question,
            answer=question.answer,
            urls=tuple(urls),
            qid=question_id(question.question, urls),
        )

    def entry(self) -> dict[str, Any]:
        """What ``run.json`` and the manifest keep of it: no question or answer text."""
        return {
            "question_id": self.qid,
            "group": self.group,
            "source": self.source,
            "index": self.index,
            "urls": list(self.urls),
        }

    @classmethod
    def from_entry(cls, entry: Mapping[str, Any]) -> Item:
        return cls(
            group=entry["group"],
            source=entry["source"],
            index=entry["index"],
            urls=tuple(entry.get("urls") or ()),
            qid=entry["question_id"],
        )


@dataclass(frozen=True)
class Task:
    """One row to fetch: a question through one path, on one backend, in one mode."""

    item: Item
    path: str
    provider: str
    mode: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.item.qid, self.path, self.provider, self.mode)


def _urls(question: Any, source: str) -> list[str]:
    meta = question.metadata.get("metadata", question.metadata)
    links = (meta if isinstance(meta, dict) else {}).get("wiki_links" if source == FRAMES else "urls") or []
    links = [str(link) for link in links if str(link).startswith("http")]
    return links[:1] if source == SIMPLEQA else links[:MAX_GIVEN_URLS]


def build_sample(full: Mapping[str, Any]) -> list[Item]:
    """The set's questions from each public set, whole and in upstream order: the same ones on every machine."""
    position = {name: {id(q): i for i, q in enumerate(dataset.questions)} for name, dataset in full.items()}

    def item(group: str, source: str, question: Any, urls: Sequence[str] = ()) -> Item:
        return Item.make(group, source, position[source][id(question)], question, urls)

    simpleqa = full[SIMPLEQA].sample(*SIMPLEQA_SAMPLE).questions
    frames = full[FRAMES].sample(*FRAMES_SAMPLE).questions
    hard = full[SEALQA_HARD].sample(SIZES[HARD], SEALQA_SEED).questions
    items = [item(EASY, SIMPLEQA, q) for q in simpleqa[: SIZES[EASY]]]
    items += [item(HARD, SEALQA_HARD, q) for q in hard]
    items += [item(SYNTHESIS, FRAMES, q) for q in frames[: SIZES[SYNTHESIS]]]
    for source, rest in ((SIMPLEQA, simpleqa[SIZES[EASY] :]), (FRAMES, frames[SIZES[SYNTHESIS] :])):
        given = [(q, _urls(q, source)) for q in rest]
        items += [item(URL_GIVEN, source, q, urls) for q, urls in given if urls][: URL_GIVEN_SIZES[source]]
    return items


def plan(items: Iterable[Item]) -> list[Task]:
    """Every row to fetch, a whole column (path, backend, mode) at a time, so a stopped run leaves whole columns."""
    items = list(items)
    columns: list[list[Task]] = [
        *([Task(i, SEARCH_EXTRACT, p, TOP_MODE) for i in items] for p in SEARCH_PROVIDERS),
        [Task(i, EXTRACT, READER, DEFAULT_MODE) for i in items if i.urls],
        *(
            [Task(i, ANSWER, p, DEFAULT_MODE) for i in items if p not in REUSED_ANSWERS.get(i.group, ())]
            for p in ANSWER_PROVIDERS
        ),
        *([Task(i, RESEARCH, p, effort) for i in items] for p, efforts in RESEARCH_EFFORTS for effort in efforts),
    ]
    return [task for column in columns for task in column]


def reused_keys(items: Iterable[Item]) -> set[tuple[str, str, str, str]]:
    """The answer rows a finished set copies from the frozen answer set rather than asking again."""
    return {(i.qid, ANSWER, provider, DEFAULT_MODE) for i in items for provider in REUSED_ANSWERS.get(i.group, ())}


def expected_keys(sample: Iterable[Mapping[str, Any]]) -> set[tuple[str, str, str, str]]:
    """Every (question_id, method, provider, mode) a finished set holds: the rows fetched and the rows reused."""
    items = [Item.from_entry(entry) for entry in sample]
    return {task.key for task in plan(items)} | reused_keys(items)
