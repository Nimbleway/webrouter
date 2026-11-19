"""The built-in ``systemone`` router: a System One decision API, as a decider.

Experimental. Opt in by name::

    WebRouter(router="systemone", router_config={"preset": "typesafe"})

System One answers typed questions about a state. Each routed call asks one
``choice`` question whose criteria are the options -- each id with its
method, backend, estimated cost and p90 latency -- about the prompt, and reads
back a probability per option and a confidence. The probabilities are the
scores and rank the options, highest first; the API's own ``choice`` is not
used. Preset: ``typesafe`` (TypeSafe's Jev, ``TYPESAFE_API_KEY``), its model
pinned, so a new release changes nothing until you change ``model``. Another
server takes ``base_url``, ``path``, ``model`` and ``api_key_env``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from web_router.constants_decider import (
    SYSTEMONE_DEFAULT_PATH,
    SYSTEMONE_PRESETS,
    SYSTEMONE_QUESTION,
    SYSTEMONE_ROUTE_TOOLS,
    SYSTEMONE_ROUTER_NAME,
)
from web_router.routing.decider import DeciderQuestion, DeciderReply
from web_router.routing.decider_http import choice_answer, post_json
from web_router.routing.decider_reply import rank
from web_router.routing.decider_router import DeciderRouter, takes_config_of
from web_router.routing.http_decider import HttpDecider


class SystemOneDecider(HttpDecider):
    """Asks a System One API one ``choice`` question per routed call, never retried."""

    router_name = SYSTEMONE_ROUTER_NAME
    presets = SYSTEMONE_PRESETS
    default_path = SYSTEMONE_DEFAULT_PATH
    default_route_tools = SYSTEMONE_ROUTE_TOOLS

    def body(self, question: DeciderQuestion) -> dict[str, Any]:
        """The System One request: the prompt and the budget as the state, and one ``choice`` question."""
        choice = {"type": "choice", "instructions": question.instructions, "criteria": dict(question.options)}
        state: dict[str, Any] = {"query": question.prompt}
        if question.budget is not None:
            state["budget"] = {k: v for k, v in asdict(question.budget).items() if v is not None}
        return {"state": state, "model": self.model, "questions": {SYSTEMONE_QUESTION: choice}}

    async def adecide(self, question: DeciderQuestion) -> DeciderReply:
        """Ask, and rank the options by the probability the API gives each."""
        data = await post_json(self._endpoint, self.body(question), question.deadline_s or self.timeout_s)
        answer, probabilities = choice_answer(data, SYSTEMONE_QUESTION, self._endpoint.host)
        return DeciderReply(ranked=rank(probabilities), scores=probabilities, confidence=answer.get("confidence"))


@takes_config_of(SystemOneDecider)
def systemone_router(**config: Any) -> DeciderRouter:
    """What ``router="systemone"`` builds from ``router_config``: a :class:`DeciderRouter` on a System One API.

    ``config`` is :class:`SystemOneDecider`'s keyword arguments, checked against its signature when built by name.
    """
    return DeciderRouter(SystemOneDecider(**config))
