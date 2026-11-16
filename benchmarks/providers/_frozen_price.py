"""A frozen row's mode as the price list keys it, read by the library's one mode parser.

Kept apart from ``frozen.py``, which re-exports it for ``route_eval_rank.py``
and ``make latency``. Imported as a sibling, by name, as ``_common`` is.
"""

from __future__ import annotations

from _common import DEPTH_MARK
from _frozen_checks import DEFAULT_MODE
from _tools_effort_plan import RESEARCH

from web_router.constants_modes import PART_SEP
from web_router.modes import legacy_parts
from web_router.prices_modes import canonical_parts


def price_mode(provider: str, method: str, mode: str) -> str | None:
    """A frozen row's ``mode`` as ``web_router.prices`` keys it, for its cost and its latency alike.

    ``default`` (the scan asked for none) is ``None``; a research row's mode is
    its effort, keyed as it is; ``@deep`` is the ``search_depth`` ``deep``;
    ``ultra-fast`` on tavily is the vendor part ``tavily-ultra-fast``, and
    ``ultra-fast@deep`` is ``tavily-ultra-fast+deep``, which has no row of its
    own. A part the price list does not name is kept, so it matches nothing.
    """
    if mode == DEFAULT_MODE:
        return None
    if method == RESEARCH:
        return mode
    vendor, _, depth = mode.partition(DEPTH_MARK)
    parts = legacy_parts(provider, vendor, depth)
    return canonical_parts(provider, method, parts) or PART_SEP.join(parts)
