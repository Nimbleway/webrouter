"""A chat model as a decider, answering ROUTE_QUESTIONS through structured outputs: ``decider="llm"``.

It calls the regular chat completions API, so it runs on any OpenAI-compatible
endpoint through ``base_url``. The ``openai`` SDK comes with the ``evals``
extra and is imported only when this decider is built, so the rest of the
package never needs it. The key is ``api_key=``, else ``OPENAI_API_KEY``; pass
``client`` to use a client you configured yourself instead.

Each answer's probabilities are scaled to sum to 1 before anything checks
them, and its ``confidence`` is the normalized one Jev reports
(:func:`~web_router.decisions.route.normalized_confidence`), so one
ROUTE_MIN_CONFIDENCE means the same thing for both.

A model's probabilities are its own report, not calibrated the way a
dedicated classifier's are: compare it with Jev on labelled rows before
trusting the same ROUTE_MIN_CONFIDENCE for both.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from web_router.constants import (
    ENV_OPENAI_API_KEY,
    EVALS_FIXED_TEMPERATURE_MODELS,
    EXTRA_EVALS,
    LLM_DECIDER_DIGEST_LENGTH,
    LLM_DECIDER_MAX_RETRIES,
    LLM_DECIDER_MODEL,
    LLM_DECIDER_PROMPT_CRITERION,
    LLM_DECIDER_PROMPT_HEAD,
    LLM_DECIDER_PROMPT_QUESTION,
    LLM_DECIDER_SCHEMA_NAME,
    LLM_DECIDER_TEMPERATURE,
    LLM_DECIDER_TIMEOUT,
    LLM_DECIDER_VERSION,
    MSG_DECISION_DECIDER_FAILED,
    MSG_ROUTE_BAD_ANSWER,
    ROUTE_DECIDER_LLM,
    ROUTE_QUESTIONS,
)
from web_router.decisions.route import Answer, normalized_confidence, normalized_probabilities
from web_router.exceptions import ProviderError, ProviderNotInstalledError
from web_router.providers._utils import resolve_api_key


class LLMDecider:
    """Asks a chat model every question in one structured-output call.

    Each answer's ``probabilities`` is the model's distribution, scaled to sum
    to 1, and its ``confidence`` the normalized confidence of its choice:
    ``(p - 1/n) / (1 - 1/n)``, the scale Jev reports.
    """

    name = ROUTE_DECIDER_LLM

    def __init__(
        self,
        model: str = LLM_DECIDER_MODEL,
        *,
        api_key: str | None = None,
        client: Any = None,
        base_url: str | None = None,
        timeout: float = LLM_DECIDER_TIMEOUT,
    ) -> None:
        """
        Args:
            model: The chat model to ask.
            api_key: The endpoint's key, for the client built here; else ``OPENAI_API_KEY``.
            client: An ``openai.OpenAI`` client to use instead of building one; then no key is read,
                and ``timeout`` is recorded as the budget but applied only to a client built here.
            base_url: Any endpoint that speaks OpenAI's chat completions, for the client built here.

        Raises:
            ProviderNotInstalledError: if the ``openai`` SDK is not installed.
            ConfigurationError: if no ``client`` is passed, and neither ``api_key`` nor
                ``OPENAI_API_KEY`` is set, here rather than on the first query.
        """
        self.model = model
        self.version = LLM_DECIDER_VERSION.format(model=model, digest=prompt_digest())
        self.timeout_s = timeout
        if client is None:
            try:
                import openai
            except ImportError as exc:
                raise ProviderNotInstalledError(self.name, EXTRA_EVALS) from exc
            key = resolve_api_key(api_key, env_var=ENV_OPENAI_API_KEY, provider=self.name)
            client = openai.OpenAI(api_key=key, base_url=base_url, timeout=timeout, max_retries=LLM_DECIDER_MAX_RETRIES)
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
                "json_schema": {"name": LLM_DECIDER_SCHEMA_NAME, "strict": True, "schema": answer_schema(questions)},
            },
        }
        if not self.model.startswith(EVALS_FIXED_TEMPERATURE_MODELS):
            request["temperature"] = LLM_DECIDER_TEMPERATURE
        try:
            response = self._client.chat.completions.create(**request)
            content = response.choices[0].message.content
        except Exception as exc:  # the SDK's errors are its own classes, and the SDK is imported lazily
            raise ProviderError(self.name, MSG_DECISION_DECIDER_FAILED.format(detail=exc), original=exc) from exc
        try:
            payload = json.loads(content)
            answers: dict[str, Answer] = {}
            for name, answer in payload.items():
                choice = str(answer["choice"])
                # Scaled before the schema's sum check: a confident answer whose rounding drifts is still an answer.
                probabilities = normalized_probabilities(answer["probabilities"])
                if choice not in probabilities:  # a choice missing from its own distribution cannot be read
                    raise KeyError(choice)
                answers[name] = Answer(
                    choice=choice,
                    confidence=normalized_confidence(probabilities, choice),
                    probabilities=probabilities,
                )
            return answers
        except (AttributeError, KeyError, TypeError, ValueError) as exc:  # any reply of the wrong shape
            raise ProviderError(self.name, MSG_ROUTE_BAD_ANSWER.format(detail=exc), original=exc) from exc

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def prompt(state: Any, questions: dict[str, dict[str, Any]]) -> str:
    """The one user message: the request, then each question's own instructions and criteria."""
    parts = [LLM_DECIDER_PROMPT_HEAD.format(query=json.dumps(state["query"]), asked_on=state.get("asked_on"))]
    for name, question in questions.items():
        parts.append(LLM_DECIDER_PROMPT_QUESTION.format(name=name, instructions=question["instructions"]))
        parts.extend(
            LLM_DECIDER_PROMPT_CRITERION.format(choice=choice, text=text)
            for choice, text in question["criteria"].items()
        )
    return "\n".join(parts)


def prompt_digest(questions: dict[str, dict[str, Any]] = ROUTE_QUESTIONS) -> str:
    """A short hash of everything that shapes the answer besides the model: the prompt and the answer schema."""
    shaped = {
        "prompt": [LLM_DECIDER_PROMPT_HEAD, LLM_DECIDER_PROMPT_QUESTION, LLM_DECIDER_PROMPT_CRITERION],
        "questions": questions,
        "schema": {"name": LLM_DECIDER_SCHEMA_NAME, "schema": answer_schema(questions)},
    }
    encoded = json.dumps(shaped, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:LLM_DECIDER_DIGEST_LENGTH]


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
