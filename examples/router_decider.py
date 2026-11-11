"""Your own decider: the one question a router answers, routed by ``DeciderRouter``.

A decider is asked one question -- the prompt, the instructions, and each
option, a candidate id such as ``search:exa`` with its method, backend,
estimated cost and p90 latency -- and answers a ranking of option ids, best
first, with optional scores and confidence. ``DeciderRouter`` does the rest:
it builds the question, checks the reply (only offered ids, each once; numbers
from 0 to 1), and turns the ranking into the decision's ``tools`` and
``providers``. Options a decider leaves out keep the static order.

This one prefers the cheapest priced option, in a few lines; a decision API or
a model goes in ``decide`` (or ``async def adecide``) the same way. Registered
by name, it is built from ``router_config``, as the built-in ``llm`` and
``systemone`` routers are. It calls nothing and needs no key.

Run it with::

    uv run python examples/router_decider.py
"""

from __future__ import annotations

import re

from web_router import DeciderQuestion, DeciderReply, DeciderRouter, WebRouter, register_router

PROMPTS = ("who won the 2022 world cup final", "read https://example.com/pricing")


def price(description: str) -> float:
    """The estimated cost in an option's description, or infinity when it is unknown."""
    found = re.search(r"\$([0-9.e-]+)", description)
    return float(found.group(1)) if found else float("inf")


class Cheapest:
    """Ranks the priced options cheapest first; an unpriced one is left out, so the static order keeps it."""

    name = "cheapest"
    timeout_s = 0.05

    def __init__(self, ceiling_usd: float = 1.0) -> None:
        self.ceiling_usd = ceiling_usd

    def decide(self, question: DeciderQuestion) -> DeciderReply:
        priced = {cid: price(about) for cid, about in question.options.items()}
        ranked = sorted((cid for cid, usd in priced.items() if usd <= self.ceiling_usd), key=priced.__getitem__)
        return DeciderReply(ranked=ranked)


def main() -> None:
    register_router("cheapest", lambda **config: DeciderRouter(Cheapest(**config)))
    with WebRouter(router="cheapest", router_config={"ceiling_usd": 0.05}) as client:
        for prompt in PROMPTS:
            plan = client.plan(prompt)
            print(f"{prompt!r}\n  -> {plan.tool} on {', '.join(plan.providers.get(plan.tool, ())) or 'none'}")
            print(f"  ({plan.decision.reason})")


if __name__ == "__main__":
    main()
