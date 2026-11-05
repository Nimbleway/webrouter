"""What the tool and effort runner's tests share: the runner loaded from its folder, its modules, and fakes.

Never a paid call: the router and the judge here answer at once, and count.
``tests/conftest.py`` registers this module as a plugin, so its fixtures reach every test module.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from web_router.evals import Question
from web_router.types import (
    Citation,
    ExtractedDocument,
    ExtractResponse,
    ResearchResponse,
    SearchResponse,
    SearchResult,
)
from web_router.types_answer import AnswerResponse

TOOLS_EFFORT_SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "tools_effort.py"
TOOLS_EFFORT_SIBLINGS = (
    "_common",
    "_frozen_checks",
    "_tools_effort_plan",
    "_tools_effort_budget",
    "_tools_effort_runner",
    "_tools_effort_run",
    "_questions",
    "_report_tables",
    "answer_report",
)
"""What the tool and effort runner imports from its folder, under plain names, so they are forgotten once it is."""


@pytest.fixture(scope="module")
def tools_effort() -> Iterator[ModuleType]:
    # Imports the plan, the checks and the cost reader as siblings, as it does when run from its folder.
    sys.path.insert(0, str(TOOLS_EFFORT_SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("tools_effort", TOOLS_EFFORT_SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Registered while it loads: its dataclasses look their module up by name.
        sys.modules["tools_effort"] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(TOOLS_EFFORT_SCRIPT.parent))
        for name in ("tools_effort", *TOOLS_EFFORT_SIBLINGS):
            sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def plan_module(tools_effort: ModuleType) -> ModuleType:
    """The plan, as the runner imported it."""
    return sys.modules["_tools_effort_plan"]


@pytest.fixture(scope="module")
def budget_module(tools_effort: ModuleType) -> ModuleType:
    """The costs and the budget, as the runner imported them."""
    return sys.modules["_tools_effort_budget"]


@pytest.fixture(scope="module")
def runner_module(tools_effort: ModuleType) -> ModuleType:
    """The judge and the rows, as the runner imported them."""
    return sys.modules["_tools_effort_runner"]


@pytest.fixture(scope="module")
def run_module(tools_effort: ModuleType) -> ModuleType:
    """The run folder and the dry run, as the runner imported them."""
    return sys.modules["_tools_effort_run"]


class FakeRouter:
    """Answers every call at once and counts them; ``raw`` carries a stated cost when one is given."""

    def __init__(self, stated: float | None = None) -> None:
        self.calls: Counter[str] = Counter()
        self.stated = stated

    def _raw(self) -> list[dict[str, Any]] | None:
        return None if self.stated is None else [{"usage": {"cost": {"total_cost": self.stated}}}]

    async def search(self, query: str, *, provider: str, max_results: int) -> SearchResponse:
        self.calls["search"] += 1
        results = [SearchResult(url=f"https://example.org/{i}", title=f"t{i}", description="snip") for i in range(5)]
        return SearchResponse(provider=provider, query=query, results=results)

    async def extract(self, url: str, *, provider: str) -> ExtractResponse:
        self.calls["extract"] += 1
        return ExtractResponse(provider=provider, documents=[ExtractedDocument(url=url, content="page text")])

    async def answer(self, query: str, *, provider: str) -> AnswerResponse:
        self.calls["answer"] += 1
        return AnswerResponse(provider=provider, query=query, answer="gold", raw=self._raw())

    async def research(self, query: str, *, provider: str, effort: str, timeout: float) -> ResearchResponse:
        self.calls["research"] += 1
        citations = [Citation(url="https://example.org/a")]
        return ResearchResponse(provider=provider, query=query, answer="gold", citations=citations, raw=self._raw())


class FakeJudge:
    """Grades everything correct, at a fixed cost per call (``None``: the call reports no usage)."""

    label = "openai:gpt-4o"

    def __init__(self, cost: float | None = 0.001) -> None:
        self.cost = cost
        self.calls = 0

    def check(self) -> None:
        pass

    async def ask(self, system: str, user: str) -> tuple[str, float | None]:
        self.calls += 1
        return ("A" if not system else "gold"), self.cost

    async def aclose(self) -> None:
        pass


def _items(plan_module: ModuleType) -> list[Any]:
    """One question of each group, with real-looking text and gold answers."""
    item = plan_module.Item.make
    return [
        item("easy", "simpleqa", 0, Question(question="Easy?", answer="gold")),
        item("hard", "sealqa-hard", 0, Question(question="Hard?", answer="gold")),
        item("synthesis", "frames", 0, Question(question="Many pages?", answer="gold")),
        item(
            "url-given",
            "frames",
            1,
            Question(question="Read these?", answer="gold"),
            ["https://a.org", "https://b.org"],
        ),
    ]


def _args(out: Path, budget: float, concurrency: int = 1) -> argparse.Namespace:
    return argparse.Namespace(out=out, budget=budget, concurrency=concurrency, research_timeout=1.0, local={})
