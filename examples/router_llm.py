"""A chat model as a router: the built-in ``router="llm"``, on a preset or any OpenAI-compatible server.

The router sends the model the prompt, the budget and each candidate --
``tool:provider`` with its estimated cost and p90 latency -- and reads back a
ranking of candidate ids, held to the offered ids by a JSON schema where the
server supports one and checked strictly either way. The model's own words are
never used: the decision's reason names the router, the first id and its score.
It waits 3 s unless ``timeout_s`` says otherwise, and orders research and
browse only unless ``route_tools`` says otherwise; ``plan()`` always asks it.

**This router sends each prompt to the model's server.**

``LLM_ROUTER_PRESET`` picks the server -- ``openai`` (the default),
``anthropic``, ``deepseek``, ``groq`` or ``openrouter`` -- and its key is that
vendor's variable, such as ``OPENAI_API_KEY``. ``LLM_ROUTER_MODEL`` names the
model, as the server spells it. With no model or key this prints what to set
and exits. Each plan bills one model call on that server.

Run it with::

    make setup   # in your webrouter clone: every extra
    LLM_ROUTER_MODEL=<model> uv run --env-file .env python examples/router_llm.py
"""

from __future__ import annotations

import os

from web_router import WebRouter
from web_router.exceptions import ConfigurationError

PRESET_ENV = "LLM_ROUTER_PRESET"
MODEL_ENV = "LLM_ROUTER_MODEL"

PROMPTS = ("who won the 2022 world cup final", "compare the EU and US rules on AI training data")


def printable(text: str | None) -> str:
    """A server's words with control characters taken out, safe to print to a terminal."""
    return "".join(ch for ch in text or "" if ch.isprintable())


def main() -> None:
    model = os.environ.get(MODEL_ENV)
    if not model:
        print(f"Set {MODEL_ENV} (and {PRESET_ENV}, openai unless set), then run again.")
        return
    config = {"preset": os.environ.get(PRESET_ENV) or "openai", "model": model}
    try:
        client = WebRouter(router="llm", router_config=config)
    except ConfigurationError as exc:  # names the variable to set, never its value
        print(exc)
        return
    with client:
        for prompt in PROMPTS:
            plan = client.plan(prompt)
            backends = printable(", ".join(plan.providers.get(plan.tool, ()))) or "none"
            print(f"{prompt!r}\n  -> {printable(plan.tool)} on {backends}\n  ({printable(plan.decision.reason)})")


if __name__ == "__main__":
    main()
