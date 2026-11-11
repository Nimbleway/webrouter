"""router-latency: how long each router takes to answer, against the latency of each tool it would order.

A router attached with ``router=`` is asked for the provider order of every
call to a tool in ``route_tools``. This times routers on route-eval's public
prompts and weighs each against each tool by the starting rule: a tool
consults the router only when the router's p50 is below 25% of the tool's
p50. Latency belongs to the router, so the result is a row per router class,
never one number. It runs outside CI: ``--dry-run`` prints what a run would
call and what it would cost, and calls nothing.

    uv run --all-extras --with scikit-learn python benchmarks/providers/router_latency.py \
        --router features --router sklearn
    uv run --all-extras python benchmarks/providers/router_latency.py --router llm --router jev \
        --llm-model <model> --llm-price 0.15,0.60 --dry-run
    uv run --all-extras python benchmarks/providers/router_latency.py --router remote:http://127.0.0.1:8765/route \
        --remote-rtt-ms 40

The prompts are the questions ``frozen.py join`` puts back; each request is
the one route-eval shows a router, with one candidate per backend the set has
rows for. Run every router you compare in one invocation: they are timed
interleaved, from one host in one session. Experimental until 1.0.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _common import DEFAULT_OUT, ENV_FILE
from dotenv import load_dotenv
from route_eval_merge import request_for
from route_eval_sets import load_set
from router_latency_args import DEFAULT_SETS, router_specs_for, settings
from router_latency_args import parse_args as _parse_args
from router_latency_e2e import added_wall_time
from router_latency_report import dry_run, environment, markdown, plan_cost
from router_latency_routers import RouterSpec
from router_latency_run import cold_builds, interleaved
from router_latency_tools import chain_orders, definition, tool_latencies

from web_router.routing.contract import RouteRequest
from web_router.routing.registry import aclose_router, discard_router

OUT_DIR = DEFAULT_OUT / "router-latency"

MSG_NOT_JOINED = (
    "{name}: no question text in {folder}; run `uv run --all-extras python benchmarks/providers/frozen.py join` "
    "first, or --dry-run to see the plan without it"
)
MSG_NO_QUESTIONS = "the chosen sets give no questions to route: {sets}"
MSG_MISSING_PROMPTS = "{name}: {count} questions have no text in {folder}; run frozen.py join again"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """The command line, described by this file's first docstring paragraph; see ``router_latency_args``."""
    return _parse_args(argv, description=__doc__.split("\n\n")[0])


def requests(sets: Sequence[str], joined: Path, *, need_prompts: bool) -> tuple[list[RouteRequest], bool]:
    """Every question of ``sets`` as route-eval's request, and whether each carries its text."""
    out: list[RouteRequest] = []
    have = joined.is_dir()
    for name in sets:
        if need_prompts and not (joined / f"{name}.jsonl").is_file():
            raise SystemExit(MSG_NOT_JOINED.format(name=name, folder=joined))
        _, questions = load_set(name, joined=joined if have and (joined / f"{name}.jsonl").is_file() else None)
        missing = sum(1 for q in questions if not q.prompt)
        if need_prompts and missing:
            raise SystemExit(MSG_MISSING_PROMPTS.format(name=name, count=missing, folder=joined))
        out += [request_for(q) for q in questions]
    if not out:
        raise SystemExit(MSG_NO_QUESTIONS.format(sets=", ".join(sets)))
    return out, all(r.prompt for r in out)


def sample(pool: Sequence[RouteRequest], count: int, seed: int) -> list[RouteRequest]:
    """``count`` requests drawn from ``pool`` in a seeded order, cycled when it is smaller."""
    shuffled = list(pool)
    random.Random(seed).shuffle(shuffled)
    return [shuffled[i % len(shuffled)] for i in range(count)]


def _phases(args: argparse.Namespace, pool: Sequence[RouteRequest]) -> tuple[list[RouteRequest], ...]:
    """The requests each phase sends: timed, cold builds, then routed against unrouted, each on its own seed."""
    timed = sample(pool, 1 + args.warmup + args.n, args.seed)
    return timed, sample(pool, args.cold, args.seed + 2), sample(pool, args.warmup + args.e2e, args.seed + 1)


def _plan(args: argparse.Namespace, specs: Sequence[RouterSpec], pool: Sequence[RouteRequest]) -> list[dict[str, Any]]:
    every = [req for phase in _phases(args, pool) for req in phase]  # priced as run, so the estimate is the run's
    return [
        {
            "name": s.name,
            "describe": s.describe,
            "calls": len(every),
            "env": list(s.env),
            "missing_env": s.missing_env(),
            "optional_env": list(s.optional_env),
            "cost_usd": plan_cost(s, every),
            "cost_note": s.cost_note,
        }
        for s in specs
    ]


def _close(routers: Iterable[Any], loop: asyncio.AbstractEventLoop | None) -> None:
    """Close every router: on the async run's loop, which its calls ran on, and then that loop; else as sync code."""
    failed: list[Exception] = []
    for router in routers:  # one that fails to close still lets the rest close
        try:
            if loop is None:
                discard_router(router)
            else:
                loop.run_until_complete(aclose_router(router))
        except Exception as exc:
            failed.append(exc)
    if loop is not None:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()
    if failed:
        raise failed[0]


def run(args: argparse.Namespace, specs: Sequence[RouterSpec], pool: Sequence[RouteRequest]) -> dict[str, Any]:
    """Every router timed interleaved, then built fresh for each cold call, then routed against unrouted."""
    tools = tool_latencies(args.chain)
    plan, cold_reqs, routed = _phases(args, pool)
    built: dict[str, Any] = {}
    # One loop for every async phase: a router's kept-alive connections belong to the loop that opened them.
    loop = asyncio.new_event_loop() if args.use_async else None
    try:
        for s in specs:
            built[s.name] = s.build()  # inside the try: one that fails still closes those built before it
        measured = interleaved(built, plan, warmup=args.warmup, deadline_s=args.deadline_s, seed=args.seed, loop=loop)
        cold = cold_builds(
            {s.name: s.build for s in specs},
            cold_reqs,
            deadline_s=args.deadline_s,
            seed=args.seed,
            loop=loop,
        )
        prompts = [r.prompt for r in routed]
        chain = chain_orders(args.chain)["search"]
        added = added_wall_time(built, prompts, chain, warmup=args.warmup, seed=args.seed, loop=loop)
    finally:
        _close(built.values(), loop)
    costs = {row["name"]: row["cost_usd"] for row in _plan(args, specs, pool)}
    return {
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "environment": environment(),
        "settings": settings(args),
        "definition": definition(args.chain, args.share),
        "tools": {name: t.summary() for name, t in tools.items()},
        "routers": {
            s.name: {
                "describe": s.describe,
                "cost_note": s.cost_note,
                "cost_usd": costs[s.name],
                **measured[s.name],
                "cold_builds": cold[s.name],
                "added": added[s.name],
            }
            for s in specs
        },
    }


def main(argv: Sequence[str] | None = None) -> None:
    # An entry point, like the scans: it may read the repo's .env, and a variable already exported wins.
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE)
    args = parse_args(sys.argv[1:] if argv is None else argv)
    specs = router_specs_for(args)
    sets = args.sets or DEFAULT_SETS
    pool, joined = requests(sets, args.joined, need_prompts=not args.dry_run)
    if args.dry_run:
        planned = {**settings(args), "requests": len(pool)}
        print(dry_run(_plan(args, specs, pool), planned, joined))
        return
    results = run(args, specs, pool)
    out = args.out or OUT_DIR / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    text = markdown(results, tool_latencies(args.chain))
    out.with_suffix(".md").write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nwrote {out} and {out.with_suffix('.md')}")


if __name__ == "__main__":
    main()
