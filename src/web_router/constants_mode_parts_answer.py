"""The vendor modes each answer backend declares, and the wire each one sends.

The answer counterpart of :mod:`web_router.constants_mode_parts`: each wire was checked against the
vendor's docs (the ``source_url`` of its price row). An answer has no depth, so these are tiers only;
none writes the question, which stays the router's.
"""

from __future__ import annotations

from collections.abc import Mapping

from web_router.modes import Tier

EXA_ANSWER_TIERS: Mapping[str, Tier] = {
    "exa-fast": Tier(wire={"model": "exa-fast"}),
    "exa-pro": Tier(wire={"model": "exa-pro"}),
    "exa-research": Tier(wire={"model": "exa-research"}),
}
"""The ``model`` values ``POST /answer`` lists past its default ``exa``."""

LINKUP_ANSWER_TIERS: Mapping[str, Tier] = {
    "linkup-flash": Tier(wire={"depth": "flash"}),
    "linkup-standard": Tier(wire={"depth": "standard"}),
    "linkup-deep": Tier(wire={"depth": "deep"}),
}
"""Linkup's depths other than ``fast``, the one an answer sends unset."""

PERPLEXITY_ANSWER_TIERS: Mapping[str, Tier] = {
    "perplexity-low": Tier(wire={"effort": "low"}),
    "perplexity-medium": Tier(wire={"effort": "medium"}),
    "perplexity-high": Tier(wire={"effort": "high"}),
}
"""The Agent API's ``effort`` levels past ``fast``, the one an answer sends unset; ``xhigh`` and ``ultra`` are left
to research, whose runs they suit."""
