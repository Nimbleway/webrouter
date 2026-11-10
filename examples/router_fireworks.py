"""Fireworks System One as a router: one typed choice question, answered with a probability per candidate.

The router posts the prompt to Fireworks' System One API with one `choice`
question whose criteria are the candidates, each `tool:provider` described by
its method, estimated cost and p90 latency. Rune, Fireworks' public System One
model, answers with a probability for every candidate; the router ranks them by
it, and the ranking becomes the decision's `tools` and `providers`, its reason
naming the first and its probability.

Below `MIN_CONFIDENCE` the router keeps no opinion: the decision names no tool,
so the call runs on the static order, recorded as `router_empty`. Confidence is
how concentrated the probabilities are, not how likely the pick is to be right,
so the floor is a starting point to tune on a benchmark, not a measured one.
It is async, and declares `timeout_s = 1.0`: past that, the call runs on the
static order.

**This router sends each prompt to Fireworks.**

Verified live on 2026-11-10: Rune answered the documented shape, a `choice`
with `probabilities` summing to 1 and a `confidence`, in about half a second
for three candidates.

The key is `FIREWORKS_API_KEY`; with none set this prints what to set and
exits. The model is pinned, so a new model behind another name does not change
the routing until you change the pin. Each plan bills one System One call, on an
HTTP client opened for it and closed after, so no connection outlives the call.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run --env-file .env python examples/router_fireworks.py

Score it against the static order and the oracle on the frozen benchmark
results with route-eval (`benchmarks/providers/README.md`), which replays the
recorded rows and calls only Fireworks, once per question. `--joined` gives the
router each question's text; without it every prompt is empty::

    uv run --all-extras --env-file .env python benchmarks/providers/route_eval.py \
        --router examples/router_fireworks.py:route --joined benchmarks/providers/results/frozen-joined
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
from typing import Any

import httpx

from web_router import RouteDecision, RouteRequest, WebRouter

KEY_ENV = "FIREWORKS_API_KEY"
BASE_URL = "https://api.fireworks.ai"
PATH = "/inference/v1/systemone"
MODEL = "accounts/fireworks/models/rune"
MIN_CONFIDENCE = 0.2

INSTRUCTIONS = (
    "Which method and backend should serve this web request? search returns links and snippets in a second; "
    "extract reads the URLs given; answer writes one answer with sources in seconds; research runs a "
    "multi-minute agentic job that bills per run; browse hands an interactive task to a browser agent."
)

PROMPTS = ("who won the 2022 world cup final", "compare the EU and US rules on AI training data")


def criteria(req: RouteRequest) -> dict[str, str]:
    """Each candidate id, described the way System One reads a choice; extract only when the prompt names a URL."""
    out = {}
    for c in (c for c in req.candidates if c.tool != "extract" or req.urls):
        cost = "unknown cost" if c.est_cost_usd is None else f"${c.est_cost_usd:g}"
        p90 = "unknown latency" if c.est_latency_s is None else f"p90 {c.est_latency_s:g}s"
        out[c.id] = f"{c.tool} on {c.provider}, {cost}, {p90}"
    return out


def probabilities(answer: Any, choices: dict[str, str]) -> dict[str, float]:
    """System One's probability for every choice, checked to be one distribution over exactly those asked.

    Raises:
        ValueError: for a reply that leaves a choice out, names one not asked, gives a number outside 0 to 1,
            or probabilities that do not sum to 1, so the call runs on the static order and the decision
            record says router_error.
    """
    numbers = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
    if set(numbers) != set(choices) or answer.get("choice") not in choices:
        raise ValueError(f"an incomplete System One reply: {sorted(numbers)} for {sorted(choices)}")
    if not all(0.0 <= p <= 1.0 for p in (*numbers.values(), float(answer.get("confidence", "nan")))):
        raise ValueError("a System One reply with a probability or confidence outside 0 to 1")  # NaN fails too
    if not math.isclose(sum(numbers.values()), 1.0, abs_tol=0.01):
        raise ValueError("a System One reply whose probabilities do not sum to 1")
    return numbers


# printable, decision and main's print loop repeat router_jev.py's on purpose: each example copies alone.
def printable(text: str | None) -> str:
    """A server's words with control characters taken out, safe to print to a terminal."""
    return "".join(ch for ch in text or "" if ch.isprintable())


def decision(req: RouteRequest, ranked: list[str], reason: str, confidence: float | None) -> RouteDecision:
    """`tools` in the order the ranking first names them, and each tool's backends in ranked order."""
    by_id = {c.id: c for c in req.candidates}
    providers: dict[str, list[str]] = {}
    for cid in ranked:
        if cid in by_id:
            providers.setdefault(by_id[cid].tool, []).append(by_id[cid].provider)
    return RouteDecision(tools=list(providers), providers=providers, confidence=confidence, reason=reason)


class FireworksRouter:
    """Ranks the candidates by Rune's probability for each, or keeps the static order when it is unsure."""

    timeout_s = 1.0

    def __init__(self, client: httpx.AsyncClient | None = None, api_key: str | None = None) -> None:
        """``client``: one you open and close yourself; without it, each call opens its own and closes it."""
        self.client = client
        self.headers = {} if client else {"Authorization": f"Bearer {api_key or os.environ[KEY_ENV]}"}

    def session(self) -> Any:
        """The client for one call: yours, left open, or a new one, closed when the call ends."""
        if self.client is not None:
            return contextlib.nullcontext(self.client)
        return httpx.AsyncClient(base_url=BASE_URL, headers=self.headers)

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        choices = criteria(req)
        if not choices:
            return RouteDecision()
        question = {"type": "choice", "instructions": INSTRUCTIONS, "criteria": choices}
        body: dict[str, Any] = {"state": {"query": req.prompt}, "model": MODEL, "questions": {"route": question}}
        async with self.session() as client:
            response = await client.post(PATH, json=body)
            response.raise_for_status()
            answer = response.json()["answers"]["route"]
        numbers = probabilities(answer, choices)
        confidence = float(answer["confidence"])
        if confidence < MIN_CONFIDENCE:
            return RouteDecision()
        ranked = sorted(choices, key=numbers.__getitem__, reverse=True)
        # The reason names the ranking's first, which the plan runs; System One's own `choice` can name another.
        return decision(req, ranked, f"fireworks: {ranked[0]}, p={numbers[ranked[0]]:.2f}", confidence)


def route(req: RouteRequest) -> RouteDecision:
    """The router as route-eval calls one: a request in, a decision out, on a client of its own."""
    return asyncio.run(FireworksRouter().aroute(req))


def main() -> None:
    if not os.environ.get(KEY_ENV):
        print(f"Set {KEY_ENV} to route with Fireworks System One, then run this again.")
        return
    with WebRouter(router=FireworksRouter()) as client:
        for prompt in PROMPTS:
            plan = client.plan(prompt)
            # Every word here can come from the server: the method, the backends and above all the reason.
            backends = printable(", ".join(plan.providers.get(plan.tool, ()))) or "none"
            print(f"{prompt!r}\n  -> {printable(plan.tool)} on {backends}\n  ({printable(plan.decision.reason)})")


if __name__ == "__main__":
    main()
