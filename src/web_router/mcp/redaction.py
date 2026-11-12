"""Taking browser-session URLs and credentials out of a result before it leaves the server.

Kept apart from :mod:`web_router.mcp.shaping`, which re-exports it, so that file
stops growing. A browse payload can carry the URL of the session the agent ran
in, and some backends put a key in it; the rules are the ``MCP_REDACT_*`` tables
in ``constants.py``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from web_router.constants import (
    MCP_MSG_REDACTED,
    MCP_REDACT_DEVTOOLS_PATTERN,
    MCP_REDACT_PLURAL,
    MCP_REDACT_QUERY_PARAMS,
    MCP_REDACT_SECRET_KEY_SUFFIXES,
    MCP_REDACT_SECRET_KEYS,
    MCP_REDACT_SESSION_ID_KEY_SUFFIXES,
    MCP_REDACT_SESSION_KEY_FRAGMENTS,
    MCP_REDACT_SESSION_KEY_WORDS,
    MCP_REDACT_URL_SCHEMES,
    MCP_REDACT_URL_SESSION_WORDS,
    MCP_REDACTED,
)

_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")
_DEVTOOLS = re.compile(MCP_REDACT_DEVTOOLS_PATTERN)
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])|([A-Z]+)([A-Z][a-z])")
_WORD = re.compile(r"[a-z0-9]+")


def redact(value: Any) -> tuple[Any, int]:
    """A copy of ``value`` with every browser-session URL and credential replaced, and how many were.

    Taken out, anywhere in the structure: everything beneath a key that names a
    credential or a backend's session id; any URL beneath a key that names a
    way into a running browser (a live view, a debugger, a CDP, websocket or
    connection endpoint); and any URL that is one on its face -- a websocket scheme, a userinfo part, a
    credential in its query, or a session word in its host or path. URLs are
    found inside longer strings too, because an agent's own notes can quote
    one. The rules are the ``MCP_REDACT_*`` tables in ``constants.py``.
    """
    return _redact(value, None)


_SECRET = "secret"
_SESSION_ID = "session_id"
_SESSION = "session"
_WHOLE = frozenset({_SECRET, _SESSION_ID})


def _redact(value: Any, context: str | None) -> tuple[Any, int]:
    if isinstance(value, dict):
        total = 0
        cleaned: dict[Any, Any] = {}
        for name, item in value.items():
            # A credential key outranks a session one, and neither is forgotten
            # by a plainer key further down: {"live": {"url": ...}} is still a live view.
            inner = context if context in _WHOLE else (_classify(str(name)) or context)
            cleaned[name], count = _redact(item, inner)
            total += count
        return cleaned, total
    if isinstance(value, (list, tuple)):
        items = [_redact(item, context) for item in value]
        cleaned_items = [item for item, _ in items]
        return (tuple(cleaned_items) if isinstance(value, tuple) else cleaned_items), sum(n for _, n in items)
    if context == _SESSION_ID and isinstance(value, (int, float)) and not isinstance(value, bool):
        return MCP_REDACTED, 1
    if not isinstance(value, str) or not value:
        return value, 0
    if context in _WHOLE or (context == _SESSION and _URL.search(value)):
        return MCP_REDACTED, 1
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        if not _is_sensitive_url(match.group(0)):
            return match.group(0)
        count += 1
        return MCP_REDACTED

    value = _URL.sub(_replace, value)
    value, devtools = _DEVTOOLS.subn(MCP_REDACTED, value)
    return value, count + devtools


def _classify(key: str) -> str | None:
    """Whether a field's name marks its contents as a credential, a session's id, a way into one, or neither."""
    words = _key_words(key)
    joined = "".join(words)
    if any(
        form in MCP_REDACT_SECRET_KEYS or form.endswith(MCP_REDACT_SECRET_KEY_SUFFIXES) for form in _singular(joined)
    ):
        return _SECRET
    if any(form.endswith(MCP_REDACT_SESSION_ID_KEY_SUFFIXES) for form in _singular(joined)):
        return _SESSION_ID
    if any(not MCP_REDACT_SESSION_KEY_WORDS.isdisjoint(_singular(word)) for word in words) or any(
        fragment in joined for fragment in MCP_REDACT_SESSION_KEY_FRAGMENTS
    ):
        return _SESSION
    return None


def _singular(word: str) -> set[str]:
    return {word, word.removesuffix(MCP_REDACT_PLURAL)}


def _key_words(key: str) -> list[str]:
    """``wsEndpoint`` -> ``["ws", "endpoint"]``; ``live_view_url`` -> ``["live", "view", "url"]``."""
    spaced = _CAMEL_BOUNDARY.sub(lambda match: " ".join(part for part in match.groups() if part), key)
    return _WORD.findall(spaced.lower())


def _is_sensitive_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        userinfo = parts.username or parts.password
        host = parts.hostname or ""
    except ValueError:
        # Unparseable is not proof of safety; a URL this layer cannot read is not sent on.
        return True
    if parts.scheme.lower() in MCP_REDACT_URL_SCHEMES or userinfo:
        return True
    if not MCP_REDACT_URL_SESSION_WORDS.isdisjoint(_WORD.findall(f"{host} {parts.path}".lower())):
        return True
    params = parse_qsl(parts.query, keep_blank_values=True) + parse_qsl(parts.fragment, keep_blank_values=True)
    return any(_normalize(name) in MCP_REDACT_QUERY_PARAMS for name, _ in params)


def _normalize(name: str) -> str:
    return name.lower().replace("_", "").replace("-", "")


def redaction_note(count: int) -> str:
    """The sentence a redacted result carries, empty when nothing was redacted."""
    return MCP_MSG_REDACTED.format(count=count, marker=MCP_REDACTED) if count else ""
