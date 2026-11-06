"""A chat model as a router, on any OpenAI-compatible server.

The router sends the model the prompt, the budget and the menu of candidates
-- each `tool:provider` with its estimated cost and p90 latency -- and reads
back `{"ranked": [...], "reason": ...}`. The answer is held to a JSON schema
whose `items.enum` is the candidate ids, so the model can only name real ones.
The ranked ids become the decision's `tools` and `providers`. It is async, and
declares `timeout_s = 3.0`: past that, the call runs on the static order.

**This router sends each prompt to the model's server.**

Any server that speaks OpenAI's chat completions with structured outputs
works: OpenAI, Together, Fireworks, a LiteLLM proxy, or a local model. The
`openai` SDK reads `OPENAI_API_KEY`, and `OPENAI_BASE_URL` for a server other
than OpenAI's; `LLM_ROUTER_MODEL` names the model, as the server spells it.
The SDK comes with the `evals` extra. With no key or model this prints what
to set and exits. Each plan bills one model call on that server.

Run it with::

    make setup   # in your webrouter clone: every extra
    LLM_ROUTER_MODEL=<model> uv run --env-file .env python examples/router_llm.py
"""

from __future__ import annotations

import json
import os
from typing import Any

from web_router import RouteDecision, RouteRequest, WebRouter

MODEL_ENV = "LLM_ROUTER_MODEL"

INSTRUCTIONS = """You route one web request to the methods and backends that should serve it.
Methods: search returns ranked links and snippets in about a second; extract reads the URLs given;
answer returns one written answer with its sources in a few seconds; research runs a multi-minute
agentic job that bills per run; browse hands an interactive task to a hosted browser agent.
Rank the candidates you would try, best first, within the budget; leave out any that do not fit.
Give the reason in a few words."""

PROMPTS = ("who won the 2022 world cup final", "compare the EU and US rules on AI training data")


def menu(req: RouteRequest) -> str:
    """The request as the model reads it: the prompt, the budget, a pinned method, and each candidate."""
    lines = [f"Request: {req.prompt}", f"Budget: {req.budget or 'none'}"]
    if req.pinned_tool:
        lines.append(f"The method is fixed to {req.pinned_tool}: rank its backends only.")
    for c in req.candidates:
        cost = "unknown" if c.est_cost_usd is None else f"${c.est_cost_usd:g}"
        p90 = "unknown" if c.est_latency_s is None else f"{c.est_latency_s:g}s"
        lines.append(f"- {c.id}: cost {cost}, p90 {p90}")
    return "\n".join(lines)


def schema(req: RouteRequest) -> dict[str, Any]:
    """Strict structured output: a ranking of real candidate ids, and a reason."""
    ranked = {"type": "array", "items": {"type": "string", "enum": [c.id for c in req.candidates]}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["ranked", "reason"],
        "properties": {"ranked": ranked, "reason": {"type": "string"}},
    }


def decision(req: RouteRequest, ranked: list[str], reason: str) -> RouteDecision:
    """`tools` in the order the ranking first names them, and each tool's backends in ranked order."""
    by_id = {c.id: c for c in req.candidates}
    providers: dict[str, list[str]] = {}
    for cid in ranked:
        if cid in by_id:
            providers.setdefault(by_id[cid].tool, []).append(by_id[cid].provider)
    return RouteDecision(tools=list(providers), providers=providers, reason=reason)


class LLMRouter:
    """Ranks the candidates with one structured-output chat completion."""

    timeout_s = 3.0

    def __init__(self, client: Any = None, model: str | None = None) -> None:
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI()  # OPENAI_API_KEY, and OPENAI_BASE_URL for any compatible server
        self.client = client
        self.model = model or os.environ[MODEL_ENV]

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": menu(req)}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "route", "strict": True, "schema": schema(req)},
            },
        )
        answer = json.loads(response.choices[0].message.content)
        return decision(req, answer["ranked"], answer["reason"])


def main() -> None:
    missing = [name for name in ("OPENAI_API_KEY", MODEL_ENV) if not os.environ.get(name)]
    if missing:
        print(f"Set {' and '.join(missing)} (and OPENAI_BASE_URL for a server other than OpenAI's), then run again.")
        return
    try:
        router = LLMRouter()
    except ImportError:
        print("This example needs the openai SDK: make setup, or pip install 'web-router[evals]'.")
        return
    with WebRouter(router=router) as client:
        for prompt in PROMPTS:
            plan = client.plan(prompt)
            backends = ", ".join(plan.providers.get(plan.tool, ())) or "none"
            print(f"{prompt!r}\n  -> {plan.tool} on {backends}\n  ({plan.decision.reason})")


if __name__ == "__main__":
    main()
