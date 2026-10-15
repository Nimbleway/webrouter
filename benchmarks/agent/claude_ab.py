"""The same four-arm comparison as ``sdk_ab.py``, on a Claude agent through the Anthropic SDK.

    A  no web tools at all
    B  Claude's built-in ``web_search``, run by Anthropic
    C  web-router's search, extract, research and browse tools
    D  C plus ``classify`` and one line asking the agent to call it first

Every arm runs the same model and the same plain prompt (``BASE_PROMPT``): a simple agent with no orchestration.
web-router's search and research put Perplexity first in the ``auto`` chain. The judge is OpenAI's, so the agent
and the grader come from different vendors.

    uv run --all-extras --group agent-bench python benchmarks/agent/claude_ab.py --limit 10 --out results/claude-dry

The tools, row shape and grading are ``sdk_ab.py``'s, so ``route_summary.py`` reads the rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from sdk_ab import (
    BASE_PROMPT,
    CLASSIFY_LINE,
    MAX_TURNS,
    WEB_TOOLS,
    WithholdingSyncRouter,
    finish_row,
    load_questions,
    new_row,
    preflight,
    web_call,
    widen_thread_pool,
)

from web_router.decisions import JevDecider
from web_router.evals.judge import OpenAIJudge

HERE = Path(__file__).parent
MODEL = "claude-haiku-5-5"
JUDGE_MODEL = "gpt-6-sol"
MAX_TOKENS = 16000
ANSWER_KEY_DOMAINS = [
    "huggingface.co/datasets/vtllms",
    "huggingface.co/papers/2506.01062",
    "arxiv.org/abs/2506.01062",
    "arxiv.org/pdf/2506.01062",
    "alphaxiv.org/abs/2506.01062",
    "drive.google.com/drive/folders/19SGV6dJkaqF9ziaoIMnx9PJwih8nDwzp",
    "colab.research.google.com/drive/1fJunutgxNaiU6-Met_wzyEdG-tXQLfrM",
]
"""SealQA's own pages (SEALQA_WITHHELD_MARKERS as paths): web-router's tools withhold them, so the built-in search
is blocked from them too. Anthropic accepts a path here (checked live 2026-10-15); any other copy is caught after the
fact by the answer-key check in sdk_ab.finish_row."""
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "blocked_domains": ANSWER_KEY_DOMAINS}
# Perplexity first for search and research, Jina first for extract; Nimble is the fallback when one fails.
CHAIN = {"search": ["perplexity", "nimble"], "extract": ["jina", "nimble"], "research": ["perplexity", "nimble"]}
QUESTIONS = "tools,scoped,seal-hard-stable"
"""Every question once: what we wrote, and all 190 stable Seal-Hard rows (not a subset any arm's misses picked)."""

TOOL_SPECS = {
    "search": {
        "description": "Search the web. Returns titles, URLs and short passages.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    "extract": {
        "description": "Read the full text of one or more known web pages.",
        "input_schema": {
            "type": "object",
            "properties": {"urls": {"type": "array", "items": {"type": "string"}}},
            "required": ["urls"],
        },
    },
    "research": {
        "description": "Run a multi-step web research job that searches many sources and writes an answer with "
        "citations.",
        "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
    },
    "browse": {
        "description": "Have a browser agent act on a website (navigate, filter, sort, fill forms) and report what "
        "it finds.",
        "input_schema": {
            "type": "object",
            "properties": {"task": {"type": "string"}, "start_url": {"type": "string"}},
            "required": ["task"],
        },
    },
    "classify": {
        "description": "Classify a web request: which tool fits it (search, extract, research or browse) and how much "
        "evidence the answer needs (lookup, synthesis or research). Call it before fetching.",
        "input_schema": {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]},
    },
}


def tool_defs(names: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{"name": name, **TOOL_SPECS[name]} for name in names]


async def run_agent(
    client: anthropic.AsyncAnthropic, router: WithholdingSyncRouter, arm: str, question: str, out: dict[str, Any]
) -> str:
    system = f"{BASE_PROMPT}\n\n{CLASSIFY_LINE}" if arm == "D" else BASE_PROMPT
    tools: list[dict[str, Any]] = {
        "A": [],
        "B": [WEB_SEARCH_TOOL],
        "C": tool_defs(WEB_TOOLS),
        "D": tool_defs((*WEB_TOOLS, "classify")),
    }[arm]
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    for _ in range(MAX_TURNS):
        params: dict[str, Any] = {"model": MODEL, "max_tokens": MAX_TOKENS, "system": system, "messages": messages}
        if tools:
            params["tools"] = tools
        response = await client.messages.create(**params)
        usage = response.usage
        out["model_calls"] += 1
        out["input_tokens"] += usage.input_tokens + (usage.cache_read_input_tokens or 0)
        out["cached_input_tokens"] += usage.cache_read_input_tokens or 0
        out["output_tokens"] += usage.output_tokens
        server = getattr(usage, "server_tool_use", None)
        out["web_searches"] += getattr(server, "web_search_requests", 0) or 0
        # The whole content goes back, thinking blocks included, so the next turn reads its own history unchanged.
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"the model stopped: {response.stop_reason}")  # a row error, not a graded answer
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text").strip()
        uses = [b for b in response.content if b.type == "tool_use"]
        results = await asyncio.gather(*(web_call(router, b.name, b.input) for b in uses))
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "content": text}
                    for b, text in zip(uses, results, strict=True)
                ],
            }
        )
    raise RuntimeError(f"max turns ({MAX_TURNS}) exceeded")


async def run_one(
    client: anthropic.AsyncAnthropic,
    router: WithholdingSyncRouter,
    arm: str,
    row: dict[str, Any],
    judge: OpenAIJudge,
    sem: asyncio.Semaphore,
) -> dict[str, Any]:
    out = new_row(arm, MODEL, row)
    async with sem:
        started = time.perf_counter()  # after the slot is taken: queue wait is not the run's latency
        try:
            out["answer"] = await run_agent(client, router, arm, row["question"], out)
        except Exception as exc:  # a failed run is a row, counted as wrong
            out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        out["latency"] = time.perf_counter() - started
        return await finish_row(out, row, judge)


async def main() -> None:
    load_dotenv()
    widen_thread_pool()
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", default="A,B,C,D")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--questions", default=QUESTIONS, help="set names from questions/sets.json, comma-separated")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "claude-ab")
    args = parser.parse_args()

    questions = load_questions(args.limit, args.seed, args.questions)
    router = WithholdingSyncRouter(decider=JevDecider(), provider_config={"auto": {"chain": CHAIN}})
    if not args.skip_preflight:
        preflight(router)
    client = anthropic.AsyncAnthropic()
    judge = OpenAIJudge(JUDGE_MODEL, temperature=None)
    judge.check()
    arms = args.arms.split(",")
    sem = asyncio.Semaphore(args.concurrency)
    args.out.mkdir(parents=True, exist_ok=True)
    rows_file = args.out / "rows.jsonl"
    print(f"model {MODEL}, judge {JUDGE_MODEL}: {len(arms)} arms x {len(questions)} questions x {args.repeats}")

    async def task(arm: str, row: dict[str, Any], repeat: int) -> None:
        out = await run_one(client, router, arm, row, judge, sem)
        out["repeat"] = repeat
        with rows_file.open("a") as handle:
            handle.write(json.dumps(out, ensure_ascii=False) + "\n")
        print(
            f"  {arm} {row['topic']:<28} r{repeat} {out['grade'] or 'ERROR':<10} {out['latency']:6.1f}s "
            f"calls={out['calls'] or out['web_searches']} {out['error'] or ''}",
            flush=True,
        )

    await asyncio.gather(*(task(arm, row, rep) for rep in range(args.repeats) for row in questions for arm in arms))
    router.close()
    print(f"wrote {rows_file}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
