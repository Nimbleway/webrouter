"""A credential's encoded spellings that plain and URL-encoded masking miss: base64, and JSON's ``\\u`` escapes.

A key can come back base64-encoded -- inside an echoed ``Basic`` authorization
header, at whatever offset the user name before it puts it -- or with some or
all of its characters written as ``\\u`` escapes by a JSON serializer.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Sequence

_B64_ALIGNMENTS = 3
"""Base64 encodes three bytes as four characters, so a key starts in one of three alignments."""

_B64_CORE_MIN = 4
"""A base64 core shorter than this could match almost any text, so it is not masked. Four keeps a key of four
characters or more covered in an error, where any key is masked; a payload only matches keys of eight or more."""


def encoded_pattern(secrets: Sequence[str]) -> re.Pattern[str] | None:
    """One pattern matching each secret base64-encoded (standard or URL-safe) or ``\\u``-escaped, or ``None``."""
    base64_forms = {form for secret in secrets if secret for form in _base64_forms(secret)}
    alternatives = [re.escape(form) for form in sorted(base64_forms, key=len, reverse=True)]
    alternatives += [_escaped(secret) for secret in sorted(set(secrets), key=len, reverse=True) if secret]
    return re.compile("|".join(alternatives)) if alternatives else None


def mask_encoded(text: str, pattern: re.Pattern[str] | None, mask: str) -> str:
    """``text`` with every match of ``pattern`` replaced by ``mask``."""
    return pattern.sub(mask, text) if pattern is not None else text


def _base64_forms(secret: str) -> set[str]:
    """The key's whole encoding, and in each alignment the characters that only the key's own bits decide.

    The characters at either end of an embedded key also carry its neighbours' bits, so only the core
    between them is fixed; masking it leaves at most a byte's worth of the key on each side.
    """
    data = secret.encode()
    forms: set[str] = set()
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        whole = encode(data).decode("ascii")
        forms |= {whole, whole.rstrip("=")}  # unpadded too, as a JWT or URL-safe token writes it
        for shift in range(_B64_ALIGNMENTS):
            encoded = encode(bytes(shift) + data).decode("ascii")
            start, end = -(-shift * 8 // 6), (shift + len(data)) * 8 // 6
            if end - start >= _B64_CORE_MIN:
                forms.add(encoded[start:end])
    return forms


def _escaped(secret: str) -> str:
    """A pattern for ``secret`` with any of its characters written as a JSON ``\\u`` escape, in either case."""
    return "".join(_escaped_char(char) for char in secret)


def _escaped_char(char: str) -> str:
    plain = re.escape(char)
    escape = rf"\\u(?i:{ord(char):04x})" if ord(char) <= 0xFFFF else ""
    solidus = r"|\\/" if char == "/" else ""
    return f"(?:{plain}|{escape}{solidus})" if escape else plain
