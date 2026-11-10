"""The built-in ``systemone`` router: a System One decision API, as a decider.

Experimental. Opt in by name::

    WebRouter(router="systemone", router_config={"preset": "fireworks"})

System One answers typed questions about a state. Each routed call asks one
``choice`` question whose criteria are the options -- each id with its
method, backend, estimated cost and p90 latency -- about the prompt, and reads
back a probability per option and a confidence. The probabilities are the
scores and rank the options, highest first; the API's own ``choice`` is not
used. Presets: ``typesafe`` (TypeSafe's Jev, ``TYPESAFE_API_KEY``) and
``fireworks`` (Fireworks' Rune, ``FIREWORKS_API_KEY``), each with its model
pinned, so a new release changes nothing until you change ``model``. Another
server takes ``base_url``, ``path``, ``model`` and ``api_key_env``.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import asdict
from typing import Any

from web_router.constants_decider import (
    DECIDER_WHY_REPLY,
    SYSTEMONE_DEFAULT_PATH,
    SYSTEMONE_PRESETS,
    SYSTEMONE_QUESTION,
    SYSTEMONE_ROUTE_TOOLS,
    SYSTEMONE_ROUTER_NAME,
    SYSTEMONE_TIMEOUT_S,
)
from web_router.routing.decider import DeciderQuestion, DeciderReply
from web_router.routing.decider_http import endpoint, post_json, preset_of, text
from web_router.routing.decider_reply import unit_number
from web_router.routing.decider_router import DeciderRouter
from web_router.routing.remote_errors import RemoteRouterInvalid


def _order(value: object) -> float:
    """A probability's sort key; anything but a number from 0 to 1 sorts last, for the reply check to refuse."""
    return float(value) if unit_number(value) else -math.inf


class SystemOneDecider:
    """Asks a System One API one ``choice`` question per routed call, never retried."""

    def __init__(
        self,
        *,
        preset: str | None = None,
        base_url: str | None = None,
        path: str | None = None,
        model: str | None = None,
        api_key_env: str | None = None,
        api_key: str | None = None,
        timeout_s: float = SYSTEMONE_TIMEOUT_S,
        route_tools: Collection[str] | None = None,
    ) -> None:
        """
        Raises:
            ConfigurationError: for an unknown preset, a preset and a base URL together or neither, no model, or
                any endpoint or key :func:`~.decider_http.endpoint` refuses.
        """
        row = preset_of(SYSTEMONE_ROUTER_NAME, SYSTEMONE_PRESETS, preset, base_url)
        if row is not None:
            base_url, preset_path, preset_model, default_env = row
            path = preset_path if path is None else path
            model = preset_model if model is None else model
            api_key_env = default_env if api_key_env is None else api_key_env
        self.model = text(model, SYSTEMONE_ROUTER_NAME, "model")
        route = text(SYSTEMONE_DEFAULT_PATH if path is None else path, SYSTEMONE_ROUTER_NAME, "path")
        self._endpoint = endpoint(
            SYSTEMONE_ROUTER_NAME,
            base_url,
            "/" + route.lstrip("/"),
            api_key=api_key,
            api_key_env=api_key_env,
            timeout_s=timeout_s,
        )
        self.timeout_s = self._endpoint.timeout_s
        self.name = f"{SYSTEMONE_ROUTER_NAME}({self._endpoint.domain})"
        self.route_tools = SYSTEMONE_ROUTE_TOOLS if route_tools is None else route_tools

    def __repr__(self) -> str:
        return f"SystemOneDecider({self._endpoint.host!r}, model={self.model!r}, timeout_s={self.timeout_s})"

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
        answers = data.get("answers") if isinstance(data, dict) else None
        answer = answers.get(SYSTEMONE_QUESTION) if isinstance(answers, dict) else None
        probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
        if not isinstance(answer, dict) or not isinstance(probabilities, dict):
            raise RemoteRouterInvalid(self._endpoint.host, DECIDER_WHY_REPLY)
        ranked = sorted(probabilities, key=lambda cid: _order(probabilities[cid]), reverse=True)
        return DeciderReply(ranked=ranked, scores=probabilities, confidence=answer.get("confidence"))


def systemone_router(**config: Any) -> DeciderRouter:
    """What ``router="systemone"`` builds from ``router_config``: a :class:`DeciderRouter` on a System One API.

    ``config`` is :class:`SystemOneDecider`'s keyword arguments, which check it.
    """
    return DeciderRouter(SystemOneDecider(**config))
