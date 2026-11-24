"""A judge by name: ``openai:gpt-4o``, ``anthropic:claude-sonnet-5-5``, ``litellm:<model>`` or ``package.module:Class``.

What a benchmark's ``--judge`` flag takes, so the judge is chosen the same way
everywhere, and a judge of your own runs from the command line as the built-in
ones do. On the vendor's own endpoint a built-in judge's name is the label its
report records, so a run's ``judge`` field names the judge to ask for again; a
judge on another endpoint is built in code, with ``base_url``.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

from web_router.constants_evals import (
    MSG_JUDGE_IMPORT,
    MSG_JUDGE_NEEDS_MODEL,
    MSG_JUDGE_NOT_A_JUDGE,
    MSG_JUDGE_SPEC,
)
from web_router.evals.judge import AnthropicJudge, Judge, OpenAIJudge
from web_router.evals.judge_litellm import LiteLLMJudge
from web_router.exceptions import ConfigurationError

BUILT_IN_JUDGES: dict[str, Callable[..., Judge]] = {
    OpenAIJudge.name: OpenAIJudge,
    AnthropicJudge.name: AnthropicJudge,
    LiteLLMJudge.name: LiteLLMJudge,
}
"""The judges named by kind: ``openai[:<model>]``, ``anthropic[:<model>]`` and ``litellm:<model>``."""


def judge_from(spec: str) -> Judge:
    """The judge ``spec`` names.

    ``openai`` and ``anthropic`` are the built-in judges on their default
    models; ``openai:<model>`` and ``anthropic:<model>`` on another.
    ``litellm:<model>`` is :class:`LiteLLMJudge` on any model LiteLLM reaches,
    named as LiteLLM names it (``litellm:gemini/gemini-3.1-flash-lite``). Anything
    else is ``package.module:Class``: a :class:`Judge` subclass, imported and
    built with no arguments, so it reads its own settings (a key, a model) from
    the environment.

    Raises:
        ConfigurationError: ``spec`` names no judge, or a LiteLLM one with no
            model; its module cannot be imported; or what it names is not a
            :class:`Judge` subclass.
    """
    kind, _, rest = spec.strip().partition(":")
    if kind == LiteLLMJudge.name and not rest:
        raise ConfigurationError(MSG_JUDGE_NEEDS_MODEL.format(spec=spec))
    if kind in BUILT_IN_JUDGES:
        return BUILT_IN_JUDGES[kind](rest) if rest else BUILT_IN_JUDGES[kind]()
    if not kind or not rest:
        raise ConfigurationError(MSG_JUDGE_SPEC.format(spec=spec))
    try:
        target = getattr(importlib.import_module(kind), rest)
    except (ImportError, AttributeError) as exc:
        raise ConfigurationError(MSG_JUDGE_IMPORT.format(spec=spec, error=exc)) from exc
    if not (isinstance(target, type) and issubclass(target, Judge)):
        raise ConfigurationError(MSG_JUDGE_NOT_A_JUDGE.format(spec=spec))
    return target()
