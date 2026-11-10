"""What the ``llm`` router sends a model and reads back: the options as text, the strict schema, the scores.

Experimental. Apart from :mod:`.decider_llm` so each stays small.
"""

from __future__ import annotations

from typing import Any

from web_router.routing.decider import DeciderQuestion


def menu(question: DeciderQuestion) -> str:
    """The request as the model reads it: the prompt, the budget and each option."""
    lines = [f"Request: {question.prompt}", f"Budget: {question.budget or 'none'}", "Options:"]
    lines.extend(f"- {cid}: {about}" for cid, about in question.options.items())
    return "\n".join(lines)


def schema(question: DeciderQuestion) -> dict[str, Any]:
    """Strict structured output: a ranking of offered ids, a score per id as a list (strict mode takes no map
    with free keys), and a confidence."""
    option = {"type": "string", "enum": list(question.options)}
    score = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "p"],
        "properties": {"id": option, "p": {"type": "number"}},
    }
    properties = {"ranked": {"type": "array", "items": option}, "scores": {"type": "array", "items": score}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["ranked", "scores", "confidence"],
        "properties": {**properties, "confidence": {"type": "number"}},
    }


def scores_of(value: object) -> object:
    """``[{"id", "p"}, ...]`` as the mapping a reply holds; anything else as it came, for the reply check to judge.

    A list with an item that is not ``{"id": str, "p": ...}`` or an id twice comes back as the list, refused.
    """
    if not isinstance(value, list):
        return value
    out: dict[object, object] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "p"} or type(item["id"]) is not str or item["id"] in out:
            return value
        out[item["id"]] = item["p"]
    return out
