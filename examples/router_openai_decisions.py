"""OpenAI's Decisions API as a decider: one typed choice question, answered with a probability per option.

``DeciderRouter`` holds the glue -- the question, the reply check, the
decision -- so this file is only the API call. Each routed call posts the
prompt as ``input`` and one ``choice`` question whose choices are the options,
each id with its method, backend, estimated cost and p90 latency. The API
answers a probability per choice and a confidence; the probabilities rank the
options and are their scores, so the reason names the first and its
probability. A refusal ranks nothing, which leaves the static order. Compare
``examples/router_jev.py``, the same question to another decision API written
standalone.

**This router sends each prompt to OpenAI.**

The key is `OPENAI_API_KEY`; with none set this prints what to set and exits.
The model is pinned. Each plan bills one Decisions call (uncached input only;
output is free), on an HTTP client opened for it and closed after.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run --env-file .env python examples/router_openai_decisions.py
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from web_router import DeciderQuestion, DeciderReply, DeciderRouter, WebRouter

KEY_ENV = "OPENAI_API_KEY"
URL = "https://api.openai.com/v1/decisions"
MODEL = "gpt-6-luna"

PROMPTS = ("who won the 2022 world cup final", "compare the EU and US rules on AI training data")


def body(question: DeciderQuestion) -> dict[str, Any]:
    """One ``choice`` question over the options, the prompt as its input."""
    choices = [{"value": cid, "description": about} for cid, about in question.options.items()]
    route = {"type": "choice", "name": "route", "instructions": question.instructions, "choices": choices}
    return {"model": MODEL, "input": question.prompt, "questions": [route]}


def reply(data: Any) -> DeciderReply:
    """The options ranked by their probability; ``DeciderRouter`` checks the ids and numbers."""
    answer = data["answers"][0]
    if answer.get("type") == "refusal":
        return DeciderReply(ranked=[])  # no opinion: the static order
    scores = {str(p["value"]): p["probability"] for p in answer["probabilities"]}
    ranked = sorted(scores, key=scores.__getitem__, reverse=True)
    return DeciderReply(ranked=ranked, scores=scores, confidence=answer.get("confidence"))


class OpenAIDecisions:
    """Asks OpenAI's Decisions API to pick among the options: one request per routed call, never retried."""

    name = "openai-decisions"
    timeout_s = 3.0  # about 0.8 s measured with a few options; more options and a new connection take longer
    route_tools = ("answer", "extract", "research", "browse")  # a plain search runs without waiting on it

    def __init__(self, api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.headers = {"Authorization": f"Bearer {api_key or os.environ[KEY_ENV]}"}
        self.transport = transport

    async def adecide(self, question: DeciderQuestion) -> DeciderReply:
        timeout = question.deadline_s or self.timeout_s
        async with httpx.AsyncClient(headers=self.headers, timeout=timeout, transport=self.transport) as client:
            response = await client.post(URL, json=body(question))
            response.raise_for_status()
            return reply(response.json())


def printable(text: str | None) -> str:
    """A server's words with control characters taken out, safe to print to a terminal."""
    return "".join(ch for ch in text or "" if ch.isprintable())


def main() -> None:
    if not os.environ.get(KEY_ENV):
        print(f"Set {KEY_ENV} to route with OpenAI's Decisions API, then run this again.")
        return
    with WebRouter(router=DeciderRouter(OpenAIDecisions())) as client:
        for prompt in PROMPTS:
            plan = client.plan(prompt)
            backends = printable(", ".join(plan.providers.get(plan.tool, ()))) or "none"
            print(f"{prompt!r}\n  -> {printable(plan.tool)} on {backends}\n  ({printable(plan.decision.reason)})")


if __name__ == "__main__":
    main()
