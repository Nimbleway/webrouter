"""A router in a few lines of rules: which method a prompt runs, with no model and no key.

A router is any function that takes a `RouteRequest` and returns a
`RouteDecision`. This one reads the prompt: a URL in it means extract, and
"compare", "report" or "analyze" means research, then search when no research
backend is configured. Anything else gets no opinion -- `tools=None` -- and
the call runs as it would with no router: a search, in `auto`'s order.

It is here to copy and change: add a rule, or rank backends as well as methods.

Runs offline with no key: `plan()` works out the method and backends without
calling any of them.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run python examples/router_rules.py
"""

from __future__ import annotations

import re

from web_router import RouteDecision, RouteRequest, WebRouter

RESEARCH_WORDS = re.compile(r"\b(compare|report|analy[sz]e)\b", re.IGNORECASE)

PROMPTS = (
    "what is retrieval augmented generation",
    "read https://en.wikipedia.org/wiki/Web_scraping",
    "compare the EU and US rules on AI training data",
)


def route(req: RouteRequest) -> RouteDecision:
    """The method a prompt runs; the backends are left in `auto`'s order."""
    if req.urls:
        return RouteDecision(tools=["extract"], reason="the prompt names a URL")
    if RESEARCH_WORDS.search(req.prompt):
        return RouteDecision(tools=["research", "search"], reason="the prompt asks for a comparison or report")
    return RouteDecision(tools=None)  # no opinion: the record says router_empty, and the static order runs


def main() -> None:
    with WebRouter(router=route) as router:
        for prompt in PROMPTS:
            plan = router.plan(prompt)
            backends = ", ".join(plan.providers.get(plan.tool, ())) or "none"
            print(f"{prompt!r}\n  -> {plan.tool} on {backends}\n  ({plan.decision.reason})")


if __name__ == "__main__":
    main()
