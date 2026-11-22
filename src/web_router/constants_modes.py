"""The mode id grammar's fixed values: its separators, its normalized levels, and its messages.

Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

from typing import get_args

from web_router.types_options import ResearchEffort, SearchDepth

ID_SEP = ":"
"""Between the parts of an id: ``tool:provider:mode``, ``provider:mode``."""
PART_SEP = "+"
"""Between the slots of a mode: ``exa-instant+lite``."""
VENDOR_SEP = "-"
"""After the provider's name in a vendor part: ``tavily-ultra-fast``."""
LEGACY_DEPTH_MARK = "@"
"""The benchmarks' old spelling of a depth on a vendor mode: ``tavily:ultra-fast@deep``."""
LEGACY_PRICE_MARK = ":"
"""The price list's old prefix on a vendor mode: ``:ultra-fast``."""

DEPTH_LEVELS: tuple[str, ...] = get_args(SearchDepth)
EFFORT_LEVELS: tuple[str, ...] = get_args(ResearchEffort)
LEVELS: frozenset[str] = frozenset(DEPTH_LEVELS + EFFORT_LEVELS)
"""The normalized levels, spelled bare: a part that is one of these fills the depth slot."""

TOOLS: tuple[str, ...] = ("search", "extract", "live_data", "answer", "research", "open_browser", "browse")
"""The router methods an id's first part may name."""

PINNED_CALLS: dict[str, tuple[str, str]] = {
    "search": ("search", "asearch"),
    "extract": ("extract", "aextract"),
    "answer": ("answer", "aanswer"),
    "browse": ("submit", "asubmit"),
}
"""The methods a mode may be pinned on, and the provider calls a pinned class stamps the mode on.

A browse task starts in ``submit``, which ``browse`` waits on, so the job it hands back keeps the pinned request."""

KIND_TIER = "tier"
KIND_CONTENT = "content"
KIND_FLAG = "flag"
KIND_DEPTH = "depth"
KIND_EFFORT = "effort"

MODE_FIELD = "mode"
"""The option an unknown or repeated mode part is reported against."""

MSG_MODE_UNKNOWN = "{provider} has no mode part {part!r}"
MSG_MODE_TWICE = "mode {mode!r} fills the {slot} slot twice: one tier, one depth or content, one flag per field"
MSG_MODE_EMPTY = "{text!r} has an empty part"
MSG_ID_SHAPE = "{text!r} is not tool:provider[:mode] or provider[:mode]"
MSG_DECLARED_PREFIX = "{owner} declares {part!r}: a vendor part starts with its provider's name, as {prefix!r}"
MSG_DECLARED_TWICE = "{owner} declares {part!r} in more than one of tiers, contents and flags"
MSG_DECLARED_NAMELESS = "{owner} declares modes but has no provider name to spell them with"
MSG_DECLARED_AS_DEPTH = "{owner} declares content {part!r} standing for {as_depth!r}, not one of {levels}"
MSG_PRICE_LEGACY = "price mode {old!r} is the old spelling of {new!r}, accepted for this release only: use {new!r}"
MSG_ROW_MODE = "price row {key}: {problem}"
ROW_NO_KIND = "a row with a mode needs its kind, and a row without one has none"
ROW_LEVEL = "a {kind} row's mode is one of {levels}"
ROW_VENDOR = "a {kind} row's mode starts with the provider's name, as {prefix!r}"
ROW_ADDS = "adds_usd and writes belong on a flag row only"
ROW_ADDS_VALUE = "adds_usd is a finite amount, not negative"
ROW_DEPTH = "depth_assumed is a search_depth level, on a tier or content row only; a content row needs it"
MSG_PIN_TOOL = "{text!r} names {tool!r}: a pinned {method} backend is {method}:provider:mode or provider:mode"
MSG_PIN_LEVEL = "mode {mode!r} names the level {level!r}, which a {method} request has no option for: drop it"
MSG_PIN_OPTION = "mode {mode!r} writes {field!r}, which provider_options sets too: drop one"
MSG_PIN_DEPTH = "mode {mode!r} stands for search_depth={depth!r}, not {asked!r}: drop one"
MSG_PIN_EFFORT = "mode {mode!r} stands for effort={effort!r}, not {asked!r}: drop one"
MSG_PIN_WRITES = "mode {mode!r} writes {field!r}, which {option}={value!r} sets to another value: drop one"
