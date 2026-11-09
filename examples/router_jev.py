"""TypeSafe's Jev as a router: one typed choice question, answered with a probability per candidate.

The router posts the prompt to Jev's System One API with one `choice` question
whose criteria are the candidates, each `tool:provider` described by its
method, estimated cost and p90 latency. Jev answers with a probability for
every candidate; the router ranks them by it, and the ranking becomes the
decision's `tools` and `providers`, its reason naming the first and its
probability. It is async, and declares `timeout_s = 1.0`: past that, the call
runs on the static order.

**This router sends each prompt to TypeSafe.**

The key is `TYPESAFE_API_KEY`; with none set this prints what to set and
exits. The model is pinned, so a new Jev release does not change the routing
until you change the pin. Each plan bills one Jev call, on an HTTP client opened
for it and closed after, so no connection outlives the call.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run --env-file .env python examples/router_jev.py
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

import httpx

from web_router import RouteDecision, RouteRequest, WebRouter

KEY_ENV = "TYPESAFE_API_KEY"
BASE_URL = "https://api.typesafe.ai"
PATH = "/v1/systemone"
MODEL = "jev-1.14.0"

INSTRUCTIONS = (
    "Which method and backend should serve this web request? search returns links and snippets in a second; "
    "extract reads the URLs given; answer writes one answer with sources in seconds; research runs a "
    "multi-minute agentic job that bills per run; browse hands an interactive task to a browser agent."
)

PROMPTS = ("who won the 2022 world cup final", "compare the EU and US rules on AI training data")


def criteria(req: RouteRequest) -> dict[str, str]:
    """Each candidate id, described the way Jev reads a choice; extract only when the prompt names a URL.

    Extract is offered with no URL in case a router supplies one; this one does not.
    """
    out = {}
    for c in (c for c in req.candidates if c.tool != "extract" or req.urls):
        cost = "unknown cost" if c.est_cost_usd is None else f"${c.est_cost_usd:g}"
        p90 = "unknown latency" if c.est_latency_s is None else f"p90 {c.est_latency_s:g}s"
        out[c.id] = f"{c.tool} on {c.provider}, {cost}, {p90}"
    return out


def probabilities(answer: Any, choices: dict[str, str]) -> dict[str, float]:
    """Jev's probability for every choice.

    Raises:
        ValueError: for a reply that leaves a choice out, names one not asked, or gives a probability or
            confidence that is not a number from 0 to 1, so the call runs on the static order and the
            decision record says router_error.
    """
    numbers = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
    if set(numbers) != set(choices) or answer.get("choice") not in choices:
        raise ValueError(f"an incomplete Jev reply: {sorted(numbers)} for {sorted(choices)}")
    if not all(0.0 <= p <= 1.0 for p in (*numbers.values(), float(answer.get("confidence", "nan")))):
        raise ValueError("a Jev reply with a probability or confidence outside 0 to 1")  # NaN fails too
    return numbers


# printable, decision and main's print loop repeat router_llm.py's on purpose: each example copies alone.
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


class JevRouter:
    """Ranks the candidates by Jev's probability for each."""

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
        ranked = sorted(choices, key=numbers.__getitem__, reverse=True)
        # The reason names the ranking's first, which the plan runs; Jev's own `choice` can name another.
        reason = f"jev: {ranked[0]}, p={numbers[ranked[0]]:.2f}"
        return decision(req, ranked, reason, float(answer["confidence"]))


def main() -> None:
    if not os.environ.get(KEY_ENV):
        print(f"Set {KEY_ENV} to route with Jev, then run this again.")
        return
    with WebRouter(router=JevRouter()) as client:
        for prompt in PROMPTS:
            plan = client.plan(prompt)
            # Every word here can come from the server: the method, the backends and above all the reason.
            backends = printable(", ".join(plan.providers.get(plan.tool, ()))) or "none"
            print(f"{prompt!r}\n  -> {printable(plan.tool)} on {backends}\n  ({printable(plan.decision.reason)})")


if __name__ == "__main__":
    main()
