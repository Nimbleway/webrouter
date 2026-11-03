"""The tools the Claude and OpenAI integrations hand a model: each one's name, description and input schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from web_router.constants import (
    INTEGRATION_EXTRACT_MAX_URLS,
    INTEGRATION_EXTRACT_TOOL_DESCRIPTION,
    INTEGRATION_EXTRACT_TOOL_NAME,
    INTEGRATION_EXTRACT_URLS_DESCRIPTION,
    INTEGRATION_METHODS,
    INTEGRATION_SEARCH_QUERY_DESCRIPTION,
    INTEGRATION_SEARCH_TOOL_DESCRIPTION,
    INTEGRATION_SEARCH_TOOL_NAME,
)
from web_router.constants_answer import (
    INTEGRATION_ANSWER_QUERY_DESCRIPTION,
    INTEGRATION_ANSWER_TOOL_DESCRIPTION,
    INTEGRATION_ANSWER_TOOL_NAME,
    INTEGRATION_OPT_IN_METHODS,
)

KNOWN_METHODS: tuple[str, ...] = INTEGRATION_METHODS + INTEGRATION_OPT_IN_METHODS
"""Every method a tool can be built for, in the order the guide introduces them."""


@dataclass(frozen=True)
class ToolSpec:
    """One tool as every vendor describes it: a name, what it does, and the JSON Schema of its input."""

    name: str
    description: str
    schema: dict[str, Any]


TOOLS: dict[str, ToolSpec] = {
    "search": ToolSpec(
        name=INTEGRATION_SEARCH_TOOL_NAME,
        description=INTEGRATION_SEARCH_TOOL_DESCRIPTION,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "description": INTEGRATION_SEARCH_QUERY_DESCRIPTION}
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "extract": ToolSpec(
        name=INTEGRATION_EXTRACT_TOOL_NAME,
        description=INTEGRATION_EXTRACT_TOOL_DESCRIPTION,
        schema={
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": INTEGRATION_EXTRACT_MAX_URLS,
                    "description": INTEGRATION_EXTRACT_URLS_DESCRIPTION,
                }
            },
            "required": ["urls"],
            "additionalProperties": False,
        },
    ),
    "answer": ToolSpec(
        name=INTEGRATION_ANSWER_TOOL_NAME,
        description=INTEGRATION_ANSWER_TOOL_DESCRIPTION,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "description": INTEGRATION_ANSWER_QUERY_DESCRIPTION}
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
}
"""The tools by method, in the order of ``KNOWN_METHODS``."""
