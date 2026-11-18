"""The vendor modes each browse backend declares, and the wire each one sends.

The browse counterpart of :mod:`web_router.constants_mode_parts`: each wire was checked against the
vendor's docs (the ``source_url`` of its price row). A browse task has no depth, so these are tiers
only; none writes the task or ``start_url``, which stay the router's.
"""

from __future__ import annotations

from collections.abc import Mapping

from web_router.modes import Tier

BROWSER_USE_MODELS = (
    "gpt-5.6-luna",
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemini-3-flash-preview",
    "o3",
    "claude-sonnet-5",
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-8",
)
"""The v2 ``llm`` values past the default ``browser-use-2.0`` that Browser Use prices per step."""

BROWSER_USE_TIERS: Mapping[str, Tier] = {
    f"browser-use-{model}": Tier(wire={"llm": model}) for model in BROWSER_USE_MODELS
}
"""The model the agent runs on, each billed at its own price per step."""

SKYVERN_TIERS: Mapping[str, Tier] = {
    "skyvern-2.0": Tier(wire={"engine": "skyvern-2.0"}),
    "skyvern-3.0": Tier(wire={"engine": "skyvern-3.0"}),
    "skyvern-openai-cua": Tier(wire={"engine": "openai-cua"}),
    "skyvern-anthropic-cua": Tier(wire={"engine": "anthropic-cua"}),
    "skyvern-ui-tars": Tier(wire={"engine": "ui-tars"}),
}
"""The ``engine`` values the run-task API lists past its default ``skyvern-1.0``."""
