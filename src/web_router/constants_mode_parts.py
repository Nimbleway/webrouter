"""The vendor modes each search backend declares: its tiers, contents and flags, and the wire each one sends.

Kept in one place so the inventory reads as a table, and apart from the provider modules, which
only point their class at their own entries. Each wire was checked against the vendor's docs (the
``source_url`` of its price row); ``verified`` in its contract says whether a live call has shown
it changing what comes back. :mod:`web_router.modes` says what a tier, a content and a flag are.
"""

from __future__ import annotations

from collections.abc import Mapping

from web_router.modes import Content, Flag, Tier

EXA_TIERS: Mapping[str, Tier] = {
    "exa-instant": Tier(wire={"type": "instant"}),
    "exa-fast": Tier(wire={"type": "fast"}),
    "exa-deep-lite": Tier(wire={"type": "deep-lite"}),
    "exa-deep": Tier(wire={"type": "deep"}),
    "exa-deep-reasoning": Tier(wire={"type": "deep-reasoning"}),
}
"""Exa's search ``type``s past its default ``auto``; ``effort`` writes the same field, so the two never combine."""
EXA_CONTENT_PARTS: Mapping[str, Content] = {
    "exa-highlights": Content(as_depth="lite", wire={"contents": {"highlights": True}})
}
"""Query-relevant highlights instead of page text: it replaces what a depth asks ``contents`` for."""

TAVILY_TIERS: Mapping[str, Tier] = {
    "tavily-fast": Tier(wire={"search_depth": "fast"}),
    "tavily-ultra-fast": Tier(wire={"search_depth": "ultra-fast"}),
}
"""Tavily's own latency tiers, not web-router's ``fast`` (which sends ``advanced``); a depth writes the same field."""

PARALLEL_TIERS: Mapping[str, Tier] = {
    "parallel-turbo": Tier(wire={"mode": "turbo"}),
    "parallel-basic": Tier(wire={"mode": "basic"}),
    "parallel-advanced": Tier(wire={"mode": "advanced"}),
}
"""``/v1/search`` modes. web-router sends ``fast`` unset, which is none of these; ``/v1``'s own default is advanced."""

LINKUP_TIERS: Mapping[str, Tier] = {
    "linkup-flash": Tier(wire={"depth": "flash"}),
    "linkup-standard": Tier(wire={"depth": "standard"}),
    "linkup-deep": Tier(wire={"depth": "deep"}),
}
"""Linkup's depths other than ``fast``, the one a search sends unset."""

PERPLEXITY_TIERS: Mapping[str, Tier] = {"perplexity-fast": Tier(wire={"search_type": "fast"})}

KEENABLE_TIERS: Mapping[str, Tier] = {"keenable-realtime": Tier(wire={"mode": "realtime"})}
"""Keenable's fastest mode; unset, a search runs in ``pro``."""

YOU_CONTENTS: Mapping[str, Content] = {
    "you-full-page-cache": Content(
        as_depth="deep", wire={"extraction": {"extraction_mode": "full_page", "extraction_source": "cache"}}
    )
}
"""Full pages from You.com's cache only: ``deep``'s extraction, without a live fetch."""

BRAVE_FLAGS: Mapping[str, Flag] = {"brave-extra-snippets": Flag(wire={"extra_snippets": True}, writes="extra_snippets")}
"""Up to five more excerpts per result."""

DEEPSEEK_FLAGS: Mapping[str, Flag] = {"deepseek-content": Flag(wire={"need_content": True}, writes="need_content")}
"""Each result's page body beside its summary; ``deep`` sends it too."""
