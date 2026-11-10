"""Redacting an echo of the remote router's bearer key from a service's decision before it is read."""

from __future__ import annotations

from typing import Any

from web_router.constants_remote import REMOTE_EXCLUDE_FIELD, REMOTE_KEY_REDACTED, REMOTE_PARAMS_FIELD


def _unechoed(value: Any, key: str, *, names: bool = False) -> Any:
    """``value`` with ``key`` redacted from every string in it, and, when ``names``, from every mapping key too."""
    if isinstance(value, str):
        return value.replace(key, REMOTE_KEY_REDACTED)
    if isinstance(value, list):
        return [_unechoed(item, key, names=names) for item in value]
    if isinstance(value, dict):
        return {(_unechoed(n, key) if names else n): _unechoed(item, key, names=names) for n, item in value.items()}
    return value  # a number, a bool or null is the decision's own


def unechoed_decision(data: Any, key: str) -> Any:
    """A decision's JSON with ``key`` redacted from its text and every name a record can show.

    Field names and the tool names keying ``providers`` and ``params`` stay whole, so a key spelled like one
    cannot turn a valid decision invalid; ``exclude``'s candidate ids and each tool's option names are redacted.
    """
    if not isinstance(data, dict):
        return data
    out = {}
    for field, item in data.items():
        if field == REMOTE_PARAMS_FIELD and isinstance(item, dict):
            out[field] = {tool: _unechoed(options, key, names=True) for tool, options in item.items()}
        else:
            out[field] = _unechoed(item, key, names=field == REMOTE_EXCLUDE_FIELD)
    return out
