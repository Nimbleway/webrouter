"""TypeSafe's Jev as a router: one typed choice question, answered with a probability per candidate.

The router posts the prompt to Jev's System One API with one `choice` question
whose criteria are the candidates, each `tool:provider` described by its
method, estimated cost and p90 latency. Jev answers with a probability for
every candidate; the router ranks them by it, and the ranking becomes the
decision's `tools` and `providers`. It is async, and declares
`timeout_s = 1.0`: past that, the call runs on the static order.

**This router sends each prompt to TypeSafe.**

The key is `TYPESAFE_API_KEY`; with none set this prints what to set and
exits. The model is pinned, so a new Jev release does not change the routing
until you change the pin. Each plan bills one Jev call.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run --env-file .env python examples/router_jev.py
"""

from __future__ import annotations

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
    """Each candidate id, described the way Jev reads a choice."""
    out = {}
    for c in req.candidates:
        cost = "unknown cost" if c.est_cost_usd is None else f"${c.est_cost_usd:g}"
        p90 = "unknown latency" if c.est_latency_s is None else f"p90 {c.est_latency_s:g}s"
        out[c.id] = f"{c.tool} on {c.provider}, {cost}, {p90}"
    return out


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
        if client is None:
            key = api_key or os.environ[KEY_ENV]
            client = httpx.AsyncClient(base_url=BASE_URL, headers={"Authorization": f"Bearer {key}"})
        self.client = client

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        choices = criteria(req)
        question = {"type": "choice", "instructions": INSTRUCTIONS, "criteria": choices}
        body: dict[str, Any] = {"state": {"query": req.prompt}, "model": MODEL, "questions": {"route": question}}
        response = await self.client.post(PATH, json=body)
        response.raise_for_status()
        answer = response.json()["answers"]["route"]
        probabilities = {k: float(v) for k, v in (answer.get("probabilities") or {}).items()}
        ranked = sorted(choices, key=lambda cid: probabilities.get(cid, 0.0), reverse=True)
        return decision(req, ranked, f"jev: {answer['choice']}", float(answer["confidence"]))


def main() -> None:
    if not os.environ.get(KEY_ENV):
        print(f"Set {KEY_ENV} to route with Jev, then run this again.")
        return
    with WebRouter(router=JevRouter()) as client:
        for prompt in PROMPTS:
            plan = client.plan(prompt)
            backends = ", ".join(plan.providers.get(plan.tool, ())) or "none"
            print(f"{prompt!r}\n  -> {plan.tool} on {backends}\n  ({plan.decision.reason})")


if __name__ == "__main__":
    main()
