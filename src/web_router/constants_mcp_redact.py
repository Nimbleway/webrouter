"""The query parameters that mark a URL as carrying a credential, for the MCP server's redaction.

Kept apart from :mod:`web_router.constants`, so that file stops growing; the
other ``MCP_REDACT_*`` tables are still there.
"""

from __future__ import annotations

MCP_REDACT_QUERY_PARAMS: frozenset[str] = frozenset(
    {
        "token",
        "apikey",
        "xapikey",
        "signingkey",
        "jwt",
        "key",
        "auth",
        "accesstoken",
        "authtoken",
        "sessiontoken",
        "refreshtoken",
        "idtoken",
        "clientsecret",
        "secret",
        "password",
        "passwd",
        "privatekey",
        "accesskey",
        "signature",
        "sig",
        "xamzsignature",
        "xamzsecuritytoken",
        "xgoogsignature",
    }
)
"""Query parameters, lowercased with ``_`` and ``-`` removed, that carry a credential inside a URL.

Matched whole, never by suffix: a ``pageToken`` or a ``csrf_token`` is not a
credential, and a URL is replaced whole. The ``x-amz-`` and ``x-goog-`` names
are a presigned URL's, which is as good as the credential that signed it.
"""
