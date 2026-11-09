"""router-latency: how long each router takes to answer, against the latency of each tool it would order.

A router attached with ``router=`` is asked for the provider order of every
call to a tool in ``route_tools``. This times routers on route-eval's public
prompts and weighs each against each tool by the starting rule: a tool
consults the router only when the router's p50 is below 25% of the tool's
p50. Latency belongs to the router, so the result is a row per router class,
never one number. It runs outside CI: ``--dry-run`` prints what a run would
call and what it would cost, and calls nothing.

    uv run --all-extras --with scikit-learn python benchmarks/providers/router_latency.py \
        --router rules --router sklearn
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
import importlib.metadata
import json
import math
import os
import platform
import random
import subprocess
import sys
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _common import DEFAULT_OUT, ENV_FILE
from dotenv import load_dotenv
from frozen import JOINED_DIR
from route_eval_merge import request_for
from route_eval_sets import load_set
from router_latency_report import dry_run, markdown
from router_latency_routers import RouterSpec, aclose, close, plan_cost, router_specs
from router_latency_run import added_wall_time, cold_builds, interleaved
from router_latency_tools import CHAIN_ALL, CHAINS, SHARE, chain_orders, definition, tool_latencies

import web_router
from web_router.routing.contract import RouteRequest

DEFAULT_SETS = ("tools-effort-60", "simpleqa-300-v2")
"""route-eval's public prompts: the mixed tool set, and SimpleQA's search questions."""
DEFAULT_ROUTERS = ("rules", "sklearn")
"""The routers that run in this process and bill nothing."""
DEFAULT_N = 200
DEFAULT_WARMUP = 5
DEFAULT_COLD = 20
DEFAULT_E2E = 100
DEFAULT_SEED = 0
OUT_DIR = DEFAULT_OUT / "router-latency"
ROUTER_PACKAGES = ("scikit-learn", "openai", "httpx")
"""The packages a router's own time depends on, recorded with the results."""

MSG_NOT_JOINED = (
    "{name}: no question text in {folder}; run `uv run --all-extras python benchmarks/providers/frozen.py join` "
    "first, or --dry-run to see the plan without it"
)
MSG_REPEATED = "each router is timed once per run; named more than once: {names}"
MSG_COUNT = "a count of calls is 0 or more; got {value}"
MSG_POSITIVE = "--e2e is 1 or more: the added wall time needs a timed call; got {value}"
MSG_RTT = "--remote-rtt-ms is a finite number of milliseconds, 0 or more; got {value!r}"
MSG_SECONDS = "--deadline-s is a finite number of seconds above 0; got {value!r}"
MSG_SHARE = "--share is a fraction above 0 and at most 1; got {value!r}"
MSG_NO_QUESTIONS = "the chosen sets give no questions to route: {sets}"
MSG_USD = "a price is a finite number of USD, 0 or more; got {value!r}"
MSG_MISSING_PROMPTS = "{name}: {count} questions have no text in {folder}; run frozen.py join again"
MSG_PRICE = "--llm-price takes IN,OUT: USD per million input and output tokens, as 0.15,0.60"


def _count(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(MSG_COUNT.format(value=value))
    return value


def _positive(text: str) -> int:
    value = _count(text)
    if value < 1:
        raise argparse.ArgumentTypeError(MSG_POSITIVE.format(value=value))
    return value


def _rtt(text: str) -> float:
    """A simulated round trip: a finite number of milliseconds, 0 or more."""
    try:
        return _usd(text)
    except argparse.ArgumentTypeError:
        raise argparse.ArgumentTypeError(MSG_RTT.format(value=text)) from None


def _seconds(text: str) -> float:
    """A deadline: a finite number of seconds above 0."""
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(MSG_SECONDS.format(value=text))
    return value


def _share(text: str) -> float:
    """The rule's share of a tool's p50: above 0, at most 1."""
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not 0 < value <= 1:  # False for NaN too
        raise argparse.ArgumentTypeError(MSG_SHARE.format(value=text))
    return value


def _price(text: str) -> tuple[float, float]:
    parts = text.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(MSG_PRICE)
    try:
        return _usd(parts[0]), _usd(parts[1])
    except argparse.ArgumentTypeError:
        raise argparse.ArgumentTypeError(MSG_PRICE) from None


def _usd(text: str) -> float:
    """A price: a finite number of 0 or more."""
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(MSG_USD.format(value=text))
    return value


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--router", dest="routers", action="append", help=f"rules, sklearn, llm, jev, remote:<url>, "
                   f"module:attr or path/file.py:attr (default: {', '.join(DEFAULT_ROUTERS)})")  # fmt: skip
    p.add_argument("--set", dest="sets", action="append", help=f"frozen set (default: {', '.join(DEFAULT_SETS)})")
    p.add_argument("--joined", type=Path, default=JOINED_DIR, help="frozen.py join's output, the question text")
    p.add_argument("-n", type=_count, default=DEFAULT_N, help="timed calls per router, after the warm-up")
    p.add_argument("--warmup", type=_count, default=DEFAULT_WARMUP, help="calls dropped after the first")
    p.add_argument("--cold", type=_count, default=DEFAULT_COLD, help="calls each on a freshly built router")
    p.add_argument("--e2e", type=_positive, default=DEFAULT_E2E, help="stubbed searches, routed against unrouted")
    p.add_argument("--deadline-s", type=_seconds, default=None, help="every call's deadline (default: each router's)")
    p.add_argument("--async", dest="use_async", action="store_true", help="call from an event loop, as acall")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="the prompt sample's and call order's seed")
    p.add_argument("--chain", choices=CHAINS, default=CHAIN_ALL, help="auto's order: every key, or the keys here")
    p.add_argument("--share", type=_share, default=SHARE, help="the rule's share of the tool's p50")
    p.add_argument("--llm-model", default=None, help="the llm router's model (default: $LLM_ROUTER_MODEL)")
    p.add_argument("--llm-price", type=_price, default=None, help="IN,OUT USD per million tokens, for the estimate")
    p.add_argument("--jev-price", type=_usd, default=None, help="USD per Jev call, for the estimate")
    p.add_argument("--remote-rtt-ms", type=_rtt, action="append", default=[], help="also time remote over this RTT")
    p.add_argument("--out", type=Path, default=None, help="results file (default: results/router-latency/<time>)")
    p.add_argument("--dry-run", action="store_true", help="print what would run and its cost; call nothing")
    return p.parse_args(argv)


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


def environment() -> dict[str, Any]:
    cpu = platform.processor()
    if sys.platform == "darwin":  # platform.processor() says only "arm" or "i386" there
        brand = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True)
        cpu = brand.stdout.strip() or cpu
    return {
        "machine": f"{cpu}, {os.cpu_count()} CPUs",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "web_router": web_router.__version__,
        "packages": {name: _version(name) for name in ROUTER_PACKAGES},
    }


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _specs(args: argparse.Namespace) -> list[RouterSpec]:
    specs: list[RouterSpec] = []
    for spec in args.routers or DEFAULT_ROUTERS:
        specs += router_specs(
            spec,
            llm_model=args.llm_model,
            llm_price=args.llm_price,
            jev_price=args.jev_price,
            rtts_ms=args.remote_rtt_ms,
        )
    names = [s.name for s in specs]
    if len(set(names)) != len(names):
        raise SystemExit(MSG_REPEATED.format(names=", ".join(sorted({n for n in names if names.count(n) > 1}))))
    return specs


def _calls(args: argparse.Namespace) -> int:
    return 1 + args.warmup + args.n + args.cold + args.warmup + args.e2e


def _plan(args: argparse.Namespace, specs: Sequence[RouterSpec], pool: Sequence[RouteRequest]) -> list[dict[str, Any]]:
    every = sample(pool, _calls(args), args.seed)
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
    if loop is None:
        for router in routers:
            close(router)
        return
    try:
        for router in routers:
            loop.run_until_complete(aclose(router))
        loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        loop.close()


def run(args: argparse.Namespace, specs: Sequence[RouterSpec], pool: Sequence[RouteRequest]) -> dict[str, Any]:
    """Every router timed interleaved, then built fresh for each cold call, then routed against unrouted."""
    tools = tool_latencies(args.chain)
    plan = sample(pool, 1 + args.warmup + args.n, args.seed)
    built: dict[str, Any] = {}
    # One loop for every async phase: a router's kept-alive connections belong to the loop that opened them.
    loop = asyncio.new_event_loop() if args.use_async else None
    try:
        for s in specs:
            built[s.name] = s.build()  # inside the try: one that fails still closes those built before it
        measured = interleaved(built, plan, warmup=args.warmup, deadline_s=args.deadline_s, seed=args.seed, loop=loop)
        cold = cold_builds(
            {s.name: s.build for s in specs},
            sample(pool, args.cold, args.seed + 2),
            deadline_s=args.deadline_s,
            seed=args.seed,
            loop=loop,
        )
        prompts = [r.prompt for r in sample(pool, args.warmup + args.e2e, args.seed + 1)]
        chain = chain_orders(args.chain)["search"]
        added = added_wall_time(built, prompts, chain, warmup=args.warmup, seed=args.seed, loop=loop)
    finally:
        _close(built.values(), loop)
    costs = {row["name"]: row["cost_usd"] for row in _plan(args, specs, pool)}
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "environment": environment(),
        "settings": _settings(args),
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


def _settings(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "sets": list(args.sets or DEFAULT_SETS),
        "n": args.n,
        "warmup": args.warmup,
        "cold": args.cold,
        "e2e": args.e2e,
        "deadline_s": args.deadline_s,
        "async": args.use_async,
        "seed": args.seed,
        "chain": args.chain,
        "share": args.share,
    }


def main(argv: Sequence[str] | None = None) -> None:
    # An entry point, like the scans: it may read the repo's .env, and a variable already exported wins.
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE)
    args = parse_args(sys.argv[1:] if argv is None else argv)
    specs = _specs(args)
    sets = args.sets or DEFAULT_SETS
    pool, joined = requests(sets, args.joined, need_prompts=not args.dry_run)
    if args.dry_run:
        settings = {**_settings(args), "requests": len(pool)}
        print(dry_run(_plan(args, specs, pool), settings, joined))
        return
    results = run(args, specs, pool)
    out = args.out or OUT_DIR / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    text = markdown(results, tool_latencies(args.chain))
    out.with_suffix(".md").write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nwrote {out} and {out.with_suffix('.md')}")


if __name__ == "__main__":
    main()
