"""The built-in ``gemini`` router: Google's Gemini Decisions API, as a decider.

Experimental. Opt in by name, with ``GEMINI_API_KEY`` set::

    WebRouter(router="gemini")

Each routed call asks the API up to two ``choice`` questions in one request:
which backend, over the options with no vendor mode, and which vendor mode,
over the mode options (shown only with ``route_modes=True``). Each option is an
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
from collections.abc import Collection, Mapping
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
    GEMINI_TIMEOUT_S,
)
from web_router.constants_routing import CANDIDATE_ID_SEPARATOR
from web_router.routing.decider import DeciderQuestion, DeciderReply
from web_router.routing.decider_http import endpoint, post_json, preset_of, text
from web_router.routing.decider_reply import score_order, unit_number
from web_router.routing.decider_router import DeciderRouter, takes_config_of
from web_router.routing.remote_errors import RemoteRouterInvalid


def split(options: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """The options per question, backends then modes, each cut to the first 255 in the static order."""
    asked: dict[str, dict[str, str]] = {GEMINI_BACKEND_QUESTION: {}, GEMINI_MODE_QUESTION: {}}
    for cid, about in options.items():
        key = GEMINI_MODE_QUESTION if cid.count(CANDIDATE_ID_SEPARATOR) > 1 else GEMINI_BACKEND_QUESTION
        if len(asked[key]) < GEMINI_MAX_OPTIONS:
            asked[key][cid] = about
    return asked


def _normalized(probabilities: dict[str, Any]) -> dict[str, Any]:
    """``probabilities`` scaled to sum to 1, a guard: the API already does. Left as is when any is not a number
    from 0 to 1, for the reply check to refuse, or when they sum to 0."""
    if not all(unit_number(p) for p in probabilities.values()):
        return probabilities
    total = math.fsum(probabilities.values())
    return {cid: p / total for cid, p in probabilities.items()} if total else probabilities


def _ranked(scores: Mapping[str, Any]) -> list[str]:
    """The ids by score, highest first, ties in the static order."""
    return sorted(scores, key=lambda cid: score_order(scores[cid]), reverse=True)


class GeminiDecider:
    """Asks the Gemini Decisions API which backend and which mode, one request per routed call, never retried."""

    route_modes = False
    """Shown backends only until modes are on by default; a caller's ``route_modes=True`` shows it each mode too."""

    def __init__(
        self,
        *,
        preset: str | None = None,
        base_url: str | None = None,
        path: str | None = None,
        model: str | None = None,
        api_key_env: str | None = None,
        api_key: str | None = None,
        timeout_s: float = GEMINI_TIMEOUT_S,
        route_tools: Collection[str] | None = None,
    ) -> None:
        """
        Raises:
            ConfigurationError: for an unknown preset, a preset and a base URL together, no model, or
                any endpoint or key :func:`~.decider_http.endpoint` refuses.
        """
        named = GEMINI_DEFAULT_PRESET if preset is None and base_url is None else preset
        row = preset_of(GEMINI_ROUTER_NAME, GEMINI_PRESETS, named, base_url)
        if row is not None:
            base_url, preset_path, preset_model, default_env = row
            path = preset_path if path is None else path
            model = preset_model if model is None else model
            api_key_env = default_env if api_key_env is None else api_key_env
        self.model = text(model, GEMINI_ROUTER_NAME, "model")
        route = text(GEMINI_DEFAULT_PATH if path is None else path, GEMINI_ROUTER_NAME, "path")
        self._endpoint = endpoint(
            GEMINI_ROUTER_NAME,
            base_url,
            "/" + route.lstrip("/"),
            api_key=api_key,
            api_key_env=api_key_env,
            timeout_s=timeout_s,
            auth=(GEMINI_AUTH_HEADER, GEMINI_AUTH_VALUE),
        )
        self.timeout_s = self._endpoint.timeout_s
        self.name = f"{GEMINI_ROUTER_NAME}({self._endpoint.domain})"
        self.route_tools = GEMINI_ROUTE_TOOLS if route_tools is None else route_tools

    def __repr__(self) -> str:
        return f"GeminiDecider({self._endpoint.host!r}, model={self.model!r}, timeout_s={self.timeout_s})"

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
            RemoteRouterInvalid: for a reply without the question's probabilities, or naming an option it did
                not ask.
        """
        answers = data.get("answers") if isinstance(data, dict) else None
        answer = answers.get(key) if isinstance(answers, dict) else None
        probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
        if (
            not isinstance(answer, dict)
            or not isinstance(probabilities, dict)
            or not set(probabilities) <= set(options)
        ):
            raise RemoteRouterInvalid(self._endpoint.host, DECIDER_WHY_REPLY)
        return _normalized(probabilities), answer.get("confidence")

    async def adecide(self, question: DeciderQuestion) -> DeciderReply:
        """Ask both questions at once; rank backends, then modes, by the probability the API gives each.

        The confidence is the backend question's, else the mode question's when only it was asked.
        """
        per_question = split(question.options)
        asked = {key: options for key, options in per_question.items() if len(options) >= GEMINI_MIN_OPTIONS}
        scores = {key: dict.fromkeys(options, 1.0) for key, options in per_question.items() if len(options) == 1}
        confidence = None
        if asked:
            data = await post_json(self._endpoint, self.body(question, asked), question.deadline_s or self.timeout_s)
            for key, options in asked.items():
                scores[key], said = self._answer(data, key, options)
                confidence = said if confidence is None else confidence
        backends = scores.get(GEMINI_BACKEND_QUESTION, {})
        modes = scores.get(GEMINI_MODE_QUESTION, {})
        return DeciderReply(
            ranked=_ranked(backends) + _ranked(modes), scores={**backends, **modes}, confidence=confidence
        )


@takes_config_of(GeminiDecider)
def gemini_router(**config: Any) -> DeciderRouter:
    """What ``router="gemini"`` builds from ``router_config``: a :class:`DeciderRouter` on the Gemini Decisions API.

    ``config`` is :class:`GeminiDecider`'s keyword arguments, checked against its signature when built by name.
    """
    return DeciderRouter(GeminiDecider(**config))
