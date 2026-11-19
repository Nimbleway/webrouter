"""How a candidate is named without its method: its *entry*, ``exa`` or with a vendor mode ``exa:exa-instant``.

Experimental. A merged order names each candidate by its entry, which ``auto``
runs as that backend in that mode; failures and hooks name an attempt the same
way. :func:`~web_router.routing.contract.candidate_id` is the method and the entry.
"""

from __future__ import annotations

from collections.abc import Collection

from web_router.constants_routing import CANDIDATE_ID_SEPARATOR


def entry_name(provider: str, mode: str | None = None) -> str:
    """``provider``'s entry in ``mode``: the provider alone at its default mode (``None``), else ``provider:mode``."""
    return provider if mode is None else f"{provider}{CANDIDATE_ID_SEPARATOR}{mode}"


def split_entry(entry: str, names: Collection[str]) -> tuple[str, str | None]:
    """``entry``'s provider and mode, ``None`` its default; an exact member name in ``names`` wins over a split."""
    if entry in names:
        return entry, None
    name, _, mode = entry.partition(CANDIDATE_ID_SEPARATOR)
    return name, mode or None
