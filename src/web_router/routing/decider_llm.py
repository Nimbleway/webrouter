"""The built-in ``llm`` router: a chat model on any OpenAI-compatible chat-completions API, as a decider.

Experimental. Opt in by name::

    WebRouter(router="llm", router_config={"preset": "openai", "model": "<model>"})

A preset fills the base URL, the key variable and how JSON is asked for:
``openai`` and ``anthropic`` (a strict ``json_schema`` whose enum is the offered
ids), ``openrouter`` (the same, where the model supports it), ``deepseek`` and
``groq`` (``json_object``). A ``base_url`` with no preset -- a local server, a
proxy -- takes ``api_key_env`` unless it is on this machine, and asks for JSON
in the prompt only. ``response_format`` (``json_schema``, ``json_object`` or
``prompt``) overrides either. Either way the reply is checked strictly
(:mod:`.decider_reply`), and the model's own words are never used. ``model``
is required: the router names no default model. Scores come as a list of
``{"id", "p"}``, which a strict schema can hold, read as one score per id.

Each call sends the model the prompt, the budget, and each option's id with its
method, backend, estimated cost and p90 latency. It is posted with httpx
directly (:mod:`.decider_http`): no SDK, so nothing such as ``OPENAI_BASE_URL``
is read from the environment.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any

from web_router.constants_decider import (
    DECIDER_WHY_REPLY,
    LLM_CHAT_PATH,
    LLM_MODES,
    LLM_PRESETS,
    LLM_REPLY_FORMAT,
    LLM_ROUTE_TOOLS,
    LLM_ROUTER_NAME,
    LLM_TIMEOUT_S,
    MODE_JSON_OBJECT,
    MODE_JSON_SCHEMA,
    MODE_PROMPT,
    MSG_DECIDER_MODE,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.decider import DeciderQuestion, DeciderReply
from web_router.routing.decider_http import endpoint, post_json, preset_of, text
from web_router.routing.decider_llm_prompt import menu, schema, scores_of
from web_router.routing.decider_router import DeciderRouter, takes_config_of
from web_router.routing.remote_errors import RemoteRouterInvalid


def _unfenced(content: str) -> str:
    """``content`` without a Markdown code fence around it, which models asked in the prompt often add."""
    text = content.strip()
    if text.startswith("```") and text.endswith("```") and "\n" in text:
        return text[text.index("\n") + 1 : -3]
    return content


def checked_mode(mode: object) -> str:
    """``mode`` when it is one of ``json_schema``, ``json_object`` or ``prompt``."""
    if not isinstance(mode, str) or mode not in LLM_MODES:
        known = ", ".join(LLM_MODES)
        raise ConfigurationError(MSG_DECIDER_MODE.format(name=LLM_ROUTER_NAME, known=known, mode=mode))
    return mode


class LLMDecider:
    """Asks a chat model to rank the options: one chat completion per routed call, never retried."""

    def __init__(
        self,
        *,
        model: str,
        preset: str | None = None,
        base_url: str | None = None,
        api_key_env: str | None = None,
        api_key: str | None = None,
        timeout_s: float = LLM_TIMEOUT_S,
        route_tools: Collection[str] | None = None,
        response_format: str | None = None,
    ) -> None:
        """
        Raises:
            ConfigurationError: for an unknown preset or ``response_format``, a preset and a base URL together or
                neither, no model, or any endpoint or key :func:`~.decider_http.endpoint` refuses.
        """
        row = preset_of(LLM_ROUTER_NAME, LLM_PRESETS, preset, base_url)
        mode = MODE_PROMPT
        if row is not None:
            base_url, default_env, mode = row
            api_key_env = default_env if api_key_env is None else api_key_env
        self.model = text(model, LLM_ROUTER_NAME, "model")
        self.mode = checked_mode(mode if response_format is None else response_format)
        self._endpoint = endpoint(
            LLM_ROUTER_NAME, base_url, LLM_CHAT_PATH, api_key=api_key, api_key_env=api_key_env, timeout_s=timeout_s
        )
        self.timeout_s = self._endpoint.timeout_s
        self.name = f"{LLM_ROUTER_NAME}({self._endpoint.domain})"
        self.route_tools = LLM_ROUTE_TOOLS if route_tools is None else route_tools

    def __repr__(self) -> str:
        return f"LLMDecider({self._endpoint.host!r}, model={self.model!r}, timeout_s={self.timeout_s})"

    def body(self, question: DeciderQuestion) -> dict[str, Any]:
        """The chat-completions request for ``question``."""
        system = f"{question.instructions}\n{LLM_REPLY_FORMAT}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": menu(question)}]
        out: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.mode == MODE_JSON_SCHEMA:
            strict = {"name": "route", "strict": True, "schema": schema(question)}
            out["response_format"] = {"type": MODE_JSON_SCHEMA, "json_schema": strict}
        elif self.mode == MODE_JSON_OBJECT:
            out["response_format"] = {"type": MODE_JSON_OBJECT}
        return out

    async def adecide(self, question: DeciderQuestion) -> DeciderReply:
        """Post one chat completion and read its message as ``{"ranked", "scores", "confidence"}``."""
        data = await post_json(self._endpoint, self.body(question), question.deadline_s or self.timeout_s)
        try:
            content = data["choices"][0]["message"]["content"]
            answer = json.loads(_unfenced(content))
        except (KeyError, IndexError, TypeError, ValueError, RecursionError):
            raise RemoteRouterInvalid(self._endpoint.host, DECIDER_WHY_REPLY) from None
        if not isinstance(answer, dict):
            raise RemoteRouterInvalid(self._endpoint.host, DECIDER_WHY_REPLY)
        ranked: Any = answer.get("ranked")  # held to the question by the reply check, whatever it is
        scores: Any = scores_of(answer.get("scores"))
        confidence = answer.get("confidence")
        return DeciderReply(ranked=ranked, scores=scores, confidence=confidence)


@takes_config_of(LLMDecider)
def llm_router(**config: Any) -> DeciderRouter:
    """What ``router="llm"`` builds from ``router_config``: a :class:`DeciderRouter` on an :class:`LLMDecider`.

    ``config`` is :class:`LLMDecider`'s keyword arguments, checked against its signature when built by name.
    """
    return DeciderRouter(LLMDecider(**config))
