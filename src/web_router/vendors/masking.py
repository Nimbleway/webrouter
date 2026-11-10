"""Masking a vendor call's secrets out of whatever it returns: an answer, an error body, a failure.

Every secret is masked as written and URL-encoded, since an error body often
echoes the request URL, query credential and all.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote, quote_plus

from web_router.constants_vendors import VENDOR_MASK, VENDOR_MASK_INSIDE_MIN


def _spellings(secret: str) -> set[str]:
    return {secret, quote(secret, safe=""), quote_plus(secret)}


def mask(text: str, secrets: Sequence[str]) -> str:
    """Replace every secret in ``text`` with ``***``, as written and URL-encoded, longest first.

    Every secret, however short: this is for text about a failure, where a
    stray ``***`` costs nothing and a leaked short key costs the key. Longest
    first, so one secret inside another cannot leave a tail behind;
    URL-encoded too, since an error body often echoes the request URL, query
    credential and all.
    """
    spellings = {form for secret in secrets if secret for form in _spellings(secret)}
    for secret in sorted(spellings, key=len, reverse=True):
        text = text.replace(secret, VENDOR_MASK)
    return text


def mask_answer(value: Any, secrets: Sequence[str]) -> Any:
    """A copy of a successful answer with every secret the vendor echoed back masked.

    A string equal to a secret is masked whatever its length; a secret of
    ``VENDOR_MASK_INSIDE_MIN`` characters or more is masked anywhere inside
    longer strings too. A shorter one is masked inside a string only as a
    whole word (``Bearer abc123``), so that a login like ``user`` does not
    turn every ``username`` into ``***name``. Keys are left alone.
    """
    exact = {form for secret in secrets if secret for form in _spellings(secret)}
    inside = [secret for secret in secrets if secret and len(secret) >= VENDOR_MASK_INSIDE_MIN]
    short = sorted({secret for secret in secrets if secret and len(secret) < VENDOR_MASK_INSIDE_MIN}, key=len)
    words = [re.compile(rf"(?<![A-Za-z0-9]){re.escape(secret)}(?![A-Za-z0-9])") for secret in reversed(short)]

    def masked(text: str) -> str:
        text = mask(text, inside)
        for word in words:
            text = word.sub(VENDOR_MASK, text)
        return text

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            return VENDOR_MASK if item in exact else masked(item)
        if isinstance(item, dict):
            return {key: walk(inner) for key, inner in item.items()}
        if isinstance(item, list):
            return [walk(inner) for inner in item]
        return item

    return walk(value)
