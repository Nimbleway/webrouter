"""The system prompt that tells an agent when to reach for which web-router tool."""

from __future__ import annotations

from collections.abc import Sequence

from web_router.constants import (
    INTEGRATION_METHODS,
    INTEGRATION_ROUTING_GUIDE_INTRO,
    INTEGRATION_ROUTING_GUIDE_METHODS,
    INTEGRATION_ROUTING_GUIDE_OUTRO,
)
from web_router.integrations._specs import KNOWN_METHODS, TOOLS


def routing_guide(methods: Sequence[str] = INTEGRATION_METHODS) -> str:
    """The system prompt that tells an agent when to reach for which web-router tool.

    One paragraph per method in ``methods``, cheapest first, between a line on
    the principle -- the cheapest tool that answers -- and one on treating web
    content as data. The Claude and OpenAI tools share their names, so the
    guide reads the same for both.
    """
    names = {method: spec.name for method, spec in TOOLS.items()}
    paragraphs = [INTEGRATION_ROUTING_GUIDE_METHODS[m].format(**names) for m in KNOWN_METHODS if m in methods]
    return "\n\n".join([INTEGRATION_ROUTING_GUIDE_INTRO, *paragraphs, INTEGRATION_ROUTING_GUIDE_OUTRO])
