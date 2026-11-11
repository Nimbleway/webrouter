"""router-latency's report: the host it ran on, the results as markdown, the dry run's plan and its cost.

Imported as a sibling, by name, as ``_common`` is. Experimental until 1.0.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from router_latency_routers import RouterSpec
from router_latency_tools import TOOLS, ToolLatency, route_tools_for, verdict

import web_router
from web_router.routing.contract import RouteRequest

NONE = "n/a"
ROUTER_PACKAGES = ("scikit-learn", "openai", "httpx")
"""The packages a router's own time depends on, recorded with the results."""


def environment() -> dict[str, Any]:
    """The host, Python and package versions a run's numbers belong to."""
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


def _ms(value: float | None) -> str:
    return NONE if value is None else f"{value:.3g}" if value < 10 else f"{value:.0f}"


def _s(value: float | None) -> str:
    return NONE if value is None else f"{value:g}"


def _rate(value: float | None) -> str:
    return NONE if value is None else f"{value:.1%}"


def _dollars(value: float | None) -> str:
    return "unknown" if value is None else f"${value:.4f}"


def _packages(env: Mapping[str, Any]) -> str:
    found = ", ".join(f"{name} {version}" for name, version in env.get("packages", {}).items() if version)
    return found or "no router packages"


def _cell(router_p50_ms: float | None, tool: ToolLatency, share: float) -> str:
    word, ratio = verdict(None if router_p50_ms is None else router_p50_ms / 1000, tool, share)
    return word if ratio is None else f"{word} ({ratio:.2%})"


def tools_table(tools: Mapping[str, ToolLatency]) -> list[str]:
    lines = [
        "| Tool | First backend in `auto` | p50 s | p90 s | Calls | Measured | p50 range across the chain, s |",
        "|---|---|--:|--:|--:|---|---|",
    ]
    for name in TOOLS:
        t = tools[name]
        spread = NONE if t.range_p50_s is None else f"{t.range_p50_s[0]:g} to {t.range_p50_s[1]:g}"
        p50 = _s(t.p50_s) if t.is_measured else "not measured"
        lines.append(
            f"| {name} | {t.head or 'none'} | {p50} | {_s(t.p90_s)} | {t.calls or NONE} | {t.measured or NONE} "
            f"| {spread} |"
        )
    return lines


def routers_table(routers: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = [
        "| Router | p50 ms | p90 ms | Timeouts | Fallbacks | First call ms | Fresh: build / call p50 ms "
        "| Added wall time p50 ms (p90) | Est. cost |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for name, r in routers.items():
        warm, first, cold, added = r["warm"], r["first_call"], r.get("cold_builds") or {}, r.get("added") or {}
        added_cell = NONE if not added else f"{_ms(added.get('added_p50_ms'))} ({_ms(added.get('added_p90_ms'))})"
        lines.append(
            f"| {name} | {_ms(warm['p50_ms'])} | {_ms(warm['p90_ms'])} | {_rate(warm['timeout_rate'])} "
            f"| {_rate(warm['fallback_rate'])} | {_ms(first['p50_ms'])} "
            f"| {_ms(cold.get('build_p50_ms'))} / {_ms(cold.get('p50_ms'))} "
            f"| {added_cell} | {_dollars(r.get('cost_usd'))} |"
        )
    return lines


def verdict_table(
    routers: Mapping[str, Mapping[str, Any]], tools: Mapping[str, ToolLatency], share: float
) -> list[str]:
    lines = [
        "| Router | " + " | ".join(TOOLS) + " | `route_tools` the rule gives (measured tools) |",
        "|---|" + "---|" * len(TOOLS) + "---|",
    ]
    for name, r in routers.items():
        p50 = r["warm"]["p50_ms"]
        cells = [_cell(p50, tools[t], share) for t in TOOLS]
        chosen = route_tools_for(None if p50 is None else p50 / 1000, tools, share)
        lines.append(f"| {name} | " + " | ".join(cells) + f" | `{tuple(chosen)!r}` |")
    return lines


def markdown(results: Mapping[str, Any], tools: Mapping[str, ToolLatency]) -> str:
    env, settings = results["environment"], results["settings"]
    share = settings["share"]
    head = [
        "## Router latency",
        "",
        f"{results['generated']}; {env['machine']}, {env['platform']}, Python {env['python']}, "
        f"web-router {env['web_router']}, {_packages(env)}. Sets {', '.join(settings['sets'])}; "
        f"each router called {settings['n']} times after a first call and {settings['warmup']} warm-up calls, "
        "interleaved in a random order "
        f"(seed {settings['seed']}), {'async' if settings['async'] else 'sync'}; {settings['cold']} fresh builds; "
        f"{settings['e2e']} stubbed searches for the added wall time.",
        "",
        results["definition"],
        "",
        "A call that reached its deadline is censored: counted as a timeout, ranked above every answer, never "
        "used as a value; an error ranks the same way. A p50 or p90 that falls on either reads n/a. Errors and "
        "timeouts both fall back to the static order.",
        "",
    ]
    body = [
        *tools_table(tools),
        "",
        *routers_table(results["routers"]),
        "",
        *verdict_table(results["routers"], tools, share),
        "",
    ]
    return "\n".join(head + body)


def dry_run(rows: Sequence[Mapping[str, Any]], settings: Mapping[str, Any], prompts_joined: bool) -> str:
    """What a run would do, router by router: no router is built and nothing is called."""
    lines = [
        f"Dry run: nothing is built or called. Sets {', '.join(settings['sets'])} "
        f"({settings['requests']} requests pooled; prompts {'joined' if prompts_joined else 'NOT joined'}).",
        f"Per router, {'async' if settings['async'] else 'sync'}: 1 first call + {settings['warmup']} warm-up + "
        f"{settings['n']} timed (interleaved), "
        f"{settings['cold']} fresh builds, {settings['warmup'] + settings['e2e']} routed stubbed searches.",
        "",
    ]
    total: float | None = 0.0
    for row in rows:
        missing = ", ".join(row["missing_env"]) or "none"
        optional = ", ".join(row["optional_env"]) or "none"
        lines += [
            f"- {row['name']}: {row['describe']}",
            f"    calls to the router: {row['calls']}; needs: {', '.join(row['env']) or 'nothing'} "
            f"(missing: {missing}; optional: {optional})",
            f"    cost: {_dollars(row['cost_usd'])} -- {row['cost_note']}",
        ]
        total = None if total is None or row["cost_usd"] is None else total + row["cost_usd"]
    lines += ["", f"Estimated total: {_dollars(total)}"]
    return "\n".join(lines)


def plan_cost(spec: RouterSpec, requests: Sequence[RouteRequest]) -> float | None:
    """The estimated cost of calling ``spec`` once per request; ``0`` for a free router, ``None`` when unknown."""
    if spec.cost_usd is None:
        return 0.0
    costs = [spec.cost_usd(req) for req in requests]
    return None if any(c is None for c in costs) else sum(c for c in costs if c is not None)
