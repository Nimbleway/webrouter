"""An OpenAI model as a decider, answering ROUTE_QUESTIONS through structured outputs.

The ``openai`` SDK comes with the ``evals`` extra and is imported only when
this decider is built, so the rest of the package never needs it. The key is
read from ``OPENAI_API_KEY`` and nowhere else; pass ``client`` to use a client
you configured yourself, an OpenAI-compatible gateway's included.

A model's probabilities are its own report, not calibrated the way a
dedicated classifier's are: compare it with Jev on labelled rows before
trusting the same ROUTE_MIN_CONFIDENCE for both.
"""

from __future__ import annotations

import json
import os
from typing import Any

from web_router.constants import (
    ENV_OPENAI_API_KEY,
    EVALS_FIXED_TEMPERATURE_MODELS,
    EXTRA_EVALS,
    MSG_DECISION_DECIDER_FAILED,
    MSG_ROUTE_BAD_ANSWER,
    OPENAI_DECIDER_MAX_RETRIES,
    OPENAI_DECIDER_MODEL,
    OPENAI_DECIDER_PROMPT_CRITERION,
    OPENAI_DECIDER_PROMPT_HEAD,
    OPENAI_DECIDER_PROMPT_QUESTION,
    OPENAI_DECIDER_SCHEMA_NAME,
    OPENAI_DECIDER_TEMPERATURE,
    OPENAI_DECIDER_TIMEOUT,
    ROUTE_DECIDER_OPENAI,
)
from web_router.decisions.route import Answer
from web_router.exceptions import ConfigurationError, ProviderError, ProviderNotInstalledError


class OpenAIDecider:
    """Asks an OpenAI chat model every question in one structured-output call.

    Each answer's ``confidence`` is the probability the model gave its own
    choice, and ``probabilities`` its whole distribution.
    """

    name = ROUTE_DECIDER_OPENAI

    def __init__(
        self,
        model: str = OPENAI_DECIDER_MODEL,
        *,
        client: Any = None,
        base_url: str | None = None,
        timeout: float = OPENAI_DECIDER_TIMEOUT,
    ) -> None:
        """
        Args:
            client: An ``openai.OpenAI`` client to use instead of building one; then no key is read.
            base_url: Any endpoint that speaks OpenAI's chat completions, for the client built here.

        Raises:
            ProviderNotInstalledError: if the ``openai`` SDK is not installed.
            ConfigurationError: if no ``client`` is passed and ``OPENAI_API_KEY`` is unset,
                here rather than on the first query.
        """
        self.model = model
        self.version = model
        if client is None:
            try:
                import openai
            except ImportError as exc:
                raise ProviderNotInstalledError(self.name, EXTRA_EVALS) from exc
            api_key = os.environ.get(ENV_OPENAI_API_KEY)
            if not api_key:
                raise ConfigurationError.missing_api_key(self.name, ENV_OPENAI_API_KEY)
            client = openai.OpenAI(
                api_key=api_key, base_url=base_url, timeout=timeout, max_retries=OPENAI_DECIDER_MAX_RETRIES
            )
            self._owns_client = True
        else:
            self._owns_client = False  # a client handed in may be shared: its owner closes it
        self._client = client

    def decide(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, Answer]:
        """Answer every question about ``state``, one request.

        Raises:
            ProviderError: on any SDK error or timeout, or an answer that cannot be read.
        """
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt(state, questions)}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": OPENAI_DECIDER_SCHEMA_NAME, "strict": True, "schema": answer_schema(questions)},
            },
        }
        if not self.model.startswith(EVALS_FIXED_TEMPERATURE_MODELS):
            request["temperature"] = OPENAI_DECIDER_TEMPERATURE
        try:
            response = self._client.chat.completions.create(**request)
            content = response.choices[0].message.content
        except Exception as exc:  # the SDK's errors are its own classes, and the SDK is imported lazily
            raise ProviderError(self.name, MSG_DECISION_DECIDER_FAILED.format(detail=exc), original=exc) from exc
        try:
            payload = json.loads(content)
            return {
                name: Answer(
                    choice=str(answer["choice"]),
                    confidence=float(answer["probabilities"][answer["choice"]]),
                    probabilities={k: float(v) for k, v in answer["probabilities"].items()},
                )
                for name, answer in payload.items()
            }
        except (AttributeError, KeyError, TypeError, ValueError) as exc:  # any reply of the wrong shape
            raise ProviderError(self.name, MSG_ROUTE_BAD_ANSWER.format(detail=exc), original=exc) from exc

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def prompt(state: Any, questions: dict[str, dict[str, Any]]) -> str:
    """The one user message: the request, then each question's own instructions and criteria."""
    parts = [OPENAI_DECIDER_PROMPT_HEAD.format(query=json.dumps(state["query"]), asked_on=state.get("asked_on"))]
    for name, question in questions.items():
        parts.append(OPENAI_DECIDER_PROMPT_QUESTION.format(name=name, instructions=question["instructions"]))
        parts.extend(
            OPENAI_DECIDER_PROMPT_CRITERION.format(choice=choice, text=text)
            for choice, text in question["criteria"].items()
        )
    return "\n".join(parts)


def answer_schema(questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The JSON Schema the model must answer in: per question, one choice and a probability for every choice.

    Built from the questions rather than from the locked schema's own JSON
    Schema: strict structured outputs need every key spelled out, and a
    probability map keyed by choice is open-ended there.
    """
    properties = {
        name: {
            "type": "object",
            "additionalProperties": False,
            "required": ["choice", "probabilities"],
            "properties": {
                "choice": {"type": "string", "enum": list(question["criteria"])},
                "probabilities": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(question["criteria"]),
                    "properties": {choice: {"type": "number"} for choice in question["criteria"]},
                },
            },
        }
        for name, question in questions.items()
    }
    return {"type": "object", "additionalProperties": False, "required": list(questions), "properties": properties}
