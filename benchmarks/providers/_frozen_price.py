"""A frozen row's mode as the price list keys it, read by the library's one mode parser.

Kept apart from ``frozen.py``, which re-exports it for ``route_eval_rank.py``
and ``make latency``. Imported as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

from _common import DEPTH_MARK, MODE_MARK, split_label
from _frozen_checks import DEFAULT_MODE
from _tools_effort_plan import RESEARCH

from web_router.constants_modes import PART_SEP
from web_router.modes import legacy_parts
from web_router.prices_modes import canonical_parts


def price_mode(provider: str, method: str, mode: str) -> str | None:
    """A frozen row's ``mode`` as ``web_router.prices`` keys it, for its cost and its latency alike.

    ``default`` (the scan asked for none) is ``None``; a research row's mode is
    its effort, keyed as it is. A canonical mode (``tavily-fast``, ``fast``,
    ``exa-instant+lite``) is kept as it is, so an older scan's bare ``tavily:fast``
    (Tavily's tier then) now reads as the ``fast`` depth. The old spellings still read:
    ``@deep`` is the ``search_depth`` ``deep``; ``ultra-fast`` on tavily is the
    vendor part ``tavily-ultra-fast``, and ``ultra-fast@deep`` is
    ``tavily-ultra-fast+deep``. A part the price list does not name is kept, so it matches nothing.
    """
    if mode == DEFAULT_MODE:
        return None
    if method == RESEARCH:
        return mode
    vendor, _, depth = mode.partition(DEPTH_MARK)
    if not depth and (canonical := canonical_parts(provider, method, vendor.split(PART_SEP))):
        return canonical
    parts = legacy_parts(provider, vendor, depth)
    return canonical_parts(provider, method, parts) or PART_SEP.join(parts)


TIER_SPELLING_RUNS = frozenset({"sealqa-hard-254-modes"})
"""Scans named before canonical mode ids, when ``provider:x`` always meant the vendor tier ``provider-x``.

Their labels are frozen as canonical ids: today a bare ``tavily:fast`` is the
``fast`` depth, but in these scans it was Tavily's ``tavily-fast`` tier.
"""


def canonical_label(run: str, label: str) -> str:
    """A scan's backend label as frozen: a tier-spelling scan's ``tavily:fast`` is ``tavily:tavily-fast``.

    Its ``nimble@deep`` is ``nimble:deep``, the depth. Any other scan's label is kept as it is.
    """
    if run not in TIER_SPELLING_RUNS:
        return label
    provider, vendor, depth = split_label(label)
    parts = legacy_parts(provider, vendor, depth)
    return provider + MODE_MARK + PART_SEP.join(parts) if parts else provider
