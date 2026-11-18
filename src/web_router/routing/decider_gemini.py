"""The built-in ``gemini`` router: Google's Gemini Decisions API, as a decider.

Experimental. Opt in by name, with ``GEMINI_API_KEY`` set::

    WebRouter(router="gemini")

Each routed call asks the API up to two ``choice`` questions in one request:
which backend, over the options with no vendor mode, and which vendor mode,
over the mode options (shown only with ``route_modes=True``, and only for a
method in ``route_tools``: add ``"search"`` for search's modes). Each option is an
id with its method, backend, estimated cost and p90 latency. The API answers a
probability per option, normalized within each question; those are the scores,
so :class:`~.decider_router.DeciderRouter` ranks a mode by its backend's
probability times its share of that backend's mode probabilities. The API's
own ``choice`` is not used. A question takes at most 255 options: past that, the
first 255 in the static order are asked, and the rest follow the ranked ones in
the static order. A lone option is not asked about. The preset ``google`` is the
default, its model pinned, so a new release changes nothing until you change
``model``; another server takes ``base_url``, ``path``, ``model`` and
``api_key_env``. The key travels only in the ``x-goog-api-key`` header.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from web_router.constants_decider import DECIDER_WHY_REPLY
from web_router.constants_decider_gemini import (
    GEMINI_AUTH_HEADER,
    GEMINI_AUTH_VALUE,
    GEMINI_BACKEND_QUESTION,
    GEMINI_DEFAULT_PATH,
    GEMINI_DEFAULT_PRESET,
    GEMINI_MAX_OPTIONS,
    GEMINI_MIN_OPTIONS,
    GEMINI_MODE_INSTRUCTIONS,
    GEMINI_MODE_QUESTION,
    GEMINI_PRESETS,
    GEMINI_ROUTE_TOOLS,
    GEMINI_ROUTER_NAME,
)
from web_router.constants_routing import CANDIDATE_ID_SEPARATOR
from web_router.routing.decider import DeciderQuestion, DeciderReply
from web_router.routing.decider_http import choice_answer, post_json
from web_router.routing.decider_reply import rank, unit_number
from web_router.routing.decider_router import DeciderRouter, takes_config_of
from web_router.routing.http_decider import HttpDecider
from web_router.routing.remote_errors import RemoteRouterInvalid


def split(options: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """The options per question in the static order: backends, then vendor modes."""
    asked: dict[str, dict[str, str]] = {GEMINI_BACKEND_QUESTION: {}, GEMINI_MODE_QUESTION: {}}
    for cid, about in options.items():
        asked[GEMINI_MODE_QUESTION if cid.count(CANDIDATE_ID_SEPARATOR) > 1 else GEMINI_BACKEND_QUESTION][cid] = about
    return asked


def _normalized(probabilities: dict[str, Any]) -> dict[str, Any]:
    """``probabilities`` scaled to sum to 1, a guard: the API already does. Left as is when any is not a number
    from 0 to 1, for the reply check to refuse, or when they sum to 0."""
    if not all(unit_number(p) for p in probabilities.values()):
        return probabilities
    total = math.fsum(probabilities.values())
    return {cid: p / total for cid, p in probabilities.items()} if total else probabilities


class GeminiDecider(HttpDecider):
    """Asks the Gemini Decisions API which backend and which mode, one request per routed call, never retried."""

    router_name = GEMINI_ROUTER_NAME
    presets = GEMINI_PRESETS
    default_path = GEMINI_DEFAULT_PATH
    default_route_tools = GEMINI_ROUTE_TOOLS
    default_preset = GEMINI_DEFAULT_PRESET
    auth = (GEMINI_AUTH_HEADER, GEMINI_AUTH_VALUE)

    def body(self, question: DeciderQuestion, asked: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
        """The request: the prompt as the input, and one ``choice`` question per entry of ``asked``."""
        instructions = {
            GEMINI_BACKEND_QUESTION: question.instructions,
            GEMINI_MODE_QUESTION: question.instructions + GEMINI_MODE_INSTRUCTIONS,
        }
        questions = {
            key: {"type": "choice", "instructions": instructions[key], "criteria": dict(options)}
            for key, options in asked.items()
        }
        return {"model": self.model, "input": question.prompt, "questions": questions}

    def _answer(self, data: object, key: str, options: Mapping[str, str]) -> tuple[dict[str, Any], Any]:
        """Question ``key``'s probabilities, normalized, and its confidence.

        Raises:
            RemoteRouterInvalid: for a reply without the question's probabilities, whose probabilities do not
                name exactly the options it asked (the API answers one per option), or whose confidence is not
                a number from 0 to 1: each question's is checked, though only the first is reported.
        """
        answer, probabilities = choice_answer(data, key, self._endpoint.host)
        confidence = answer.get("confidence")
        if set(probabilities) != set(options) or not (confidence is None or unit_number(confidence)):
            raise RemoteRouterInvalid(self._endpoint.host, DECIDER_WHY_REPLY)
        return _normalized(probabilities), confidence

    async def adecide(self, question: DeciderQuestion) -> DeciderReply:
        """Ask both questions at once; rank backends, then modes, by the probability the API gives each.

        An option past a question's limit scores 0, so it follows the ranked ones in the static order. The
        confidence is the first question asked's: the backend question's, else the mode question's.
        """
        per_question = split(question.options)
        asked = {key: dict(list(options.items())[:GEMINI_MAX_OPTIONS]) for key, options in per_question.items()}
        asked = {key: options for key, options in asked.items() if len(options) >= GEMINI_MIN_OPTIONS}
        scores = {key: dict.fromkeys(options, 1.0) for key, options in per_question.items() if len(options) == 1}
        said: list[Any] = [None]
        if asked:
            data = await post_json(self._endpoint, self.body(question, asked), question.deadline_s or self.timeout_s)
            said = []
            for key, options in asked.items():
                scores[key], confidence = self._answer(data, key, options)
                said.append(confidence)
        ranked: list[str] = []
        for key, options in per_question.items():
            got = scores[key] = {cid: scores.get(key, {}).get(cid, 0.0) for cid in options}
            ranked += rank(got)
        return DeciderReply(
            ranked=ranked, scores={cid: p for got in scores.values() for cid, p in got.items()}, confidence=said[0]
        )


@takes_config_of(GeminiDecider)
def gemini_router(**config: Any) -> DeciderRouter:
    """What ``router="gemini"`` builds from ``router_config``: a :class:`DeciderRouter` on the Gemini Decisions API.

    ``config`` is :class:`GeminiDecider`'s keyword arguments, checked against its signature when built by name.
    """
    return DeciderRouter(GeminiDecider(**config))
