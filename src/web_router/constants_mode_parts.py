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
    "exa-instant": Tier(wire={"type": "instant"}, does="Exa's instant search, its fastest"),
    "exa-fast": Tier(wire={"type": "fast"}, does="Exa's fast search, quicker than its default auto"),
    "exa-deep-lite": Tier(wire={"type": "deep-lite"}, does="Exa's lighter deep search"),
    "exa-deep": Tier(wire={"type": "deep"}, does="Exa's deep search, slower and more thorough"),
    "exa-deep-reasoning": Tier(wire={"type": "deep-reasoning"}, does="Exa's deep search with reasoning, its slowest"),
}
"""Exa's search ``type``s past its default ``auto``; ``effort`` writes the same field, so the two never combine."""
EXA_CONTENT_PARTS: Mapping[str, Content] = {
    "exa-highlights": Content(
        as_depth="lite", wire={"contents": {"highlights": True}}, does="Query-relevant highlights instead of page text"
    )
}
"""Query-relevant highlights instead of page text: it replaces what a depth asks ``contents`` for."""

TAVILY_TIERS: Mapping[str, Tier] = {
    "tavily-fast": Tier(wire={"search_depth": "fast"}, does="Tavily's fast search, lower latency"),
    "tavily-ultra-fast": Tier(
        wire={"search_depth": "ultra-fast"}, does="Tavily's ultra-fast search, its lowest latency"
    ),
}
"""Tavily's own latency tiers, not web-router's ``fast`` (which sends ``advanced``); a depth writes the same field."""

PARALLEL_TIERS: Mapping[str, Tier] = {
    "parallel-turbo": Tier(wire={"mode": "turbo"}, does="Parallel's turbo search, its fastest"),
    "parallel-basic": Tier(wire={"mode": "basic"}, does="Parallel's basic search"),
    "parallel-advanced": Tier(wire={"mode": "advanced"}, does="Parallel's advanced search, its most thorough"),
}
"""``/v1/search`` modes. web-router sends ``fast`` unset, which is none of these; ``/v1``'s own default is advanced."""

LINKUP_TIERS: Mapping[str, Tier] = {
    "linkup-flash": Tier(wire={"depth": "flash"}, does="Linkup's flash search, its fastest"),
    "linkup-standard": Tier(wire={"depth": "standard"}, does="Linkup's standard search"),
    "linkup-deep": Tier(wire={"depth": "deep"}, does="Linkup's deep search, slower and more thorough"),
}
"""Linkup's depths other than ``fast``, the one a search sends unset."""

PERPLEXITY_TIERS: Mapping[str, Tier] = {
    "perplexity-fast": Tier(wire={"search_type": "fast"}, does="Perplexity's fast search")
}

KEENABLE_TIERS: Mapping[str, Tier] = {
    "keenable-realtime": Tier(wire={"mode": "realtime"}, does="Keenable's realtime search, its fastest")
}
"""Keenable's fastest mode; unset, a search runs in ``pro``."""

YOU_CONTENTS: Mapping[str, Content] = {
    "you-full-page-cache": Content(
        as_depth="deep",
        wire={"extraction": {"extraction_mode": "full_page", "extraction_source": "cache"}},
        does="Full pages from You.com's cache, without a live fetch",
    )
}
"""Full pages from You.com's cache only: ``deep``'s extraction, without a live fetch."""

BRAVE_FLAGS: Mapping[str, Flag] = {
    "brave-extra-snippets": Flag(
        wire={"extra_snippets": True}, writes="extra_snippets", does="Up to five more excerpts per result"
    )
}
"""Up to five more excerpts per result."""

DEEPSEEK_FLAGS: Mapping[str, Flag] = {
    "deepseek-content": Flag(
        wire={"need_content": True}, writes="need_content", does="Each result's page body beside its summary"
    )
}
"""Each result's page body beside its summary; ``deep`` sends it too."""
