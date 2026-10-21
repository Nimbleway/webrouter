"""The llm decider's prompt and answer schema, built from ROUTE_QUESTIONS, and the digest that versions them."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from web_router.constants_decisions import (
    LLM_DECIDER_DIGEST_LENGTH,
    LLM_DECIDER_PROMPT_CRITERION,
    LLM_DECIDER_PROMPT_HEAD,
    LLM_DECIDER_PROMPT_QUESTION,
    LLM_DECIDER_SCHEMA_NAME,
    ROUTE_QUESTIONS,
)


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

    Built from the questions: strict structured outputs need every key spelled
    out, so each choice gets its own probability field.
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
