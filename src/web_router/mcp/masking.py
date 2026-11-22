"""Masking every configured credential's value out of what the server hands a model.

:func:`~web_router.mcp.redaction.redact` finds a secret by where it sits -- a
field named for one, a key-bearing URL -- so a backend that echoes a key as
``invalid key <KEY>`` or ``Bearer <KEY>`` in plain text would slip past it.
This masks by value instead: every credential set in this process, wherever
it appears, as :func:`~web_router.vendors.masking.mask_answer` masks a
vendor's own, and in the encodings :mod:`~web_router.mcp.masking_forms` covers.
"""

from __future__ import annotations

import json
import os
from typing import Any

from web_router.constants import ENV_AGENTCORE_GATEWAY_URL
from web_router.constants_vendors import VENDOR_MASK, VENDOR_MASK_INSIDE_MIN
from web_router.mcp.env_credentials import OPTIONAL_KEYS, credential_variables, free_variables
from web_router.mcp.masking_forms import encoded_pattern, mask_encoded
from web_router.mcp.shaping import render
from web_router.vendors.masking import answer_masker, map_strings, mask

_NOT_SECRET = frozenset({ENV_AGENTCORE_GATEWAY_URL})
"""Variables a file may set that are no secret: AgentCore's gateway URL is an address, signed for with the AWS
credential, and an error about a malformed one must show it."""


def configured_secrets() -> list[str]:
    """The value of every credential variable set here, less a vendor's settings -- a region like ``us5`` is no secret.

    An optional key, such as Jina's, bills nothing but is still a key, so it is masked;
    a setting in :data:`_NOT_SECRET` is not, so an error can still show it.
    """
    names = credential_variables() - (free_variables() - set(OPTIONAL_KEYS)) - _NOT_SECRET
    return [value for name in sorted(names) if (value := os.environ.get(name))]


def mask_configured_text(text: str) -> str:
    """``text`` -- a failure's message -- with every configured credential masked anywhere, however short."""
    secrets = configured_secrets()
    return mask_encoded(mask(text, secrets), encoded_pattern(secrets), VENDOR_MASK)


def mask_configured(value: Any) -> Any:
    """A copy of a payload with every configured credential's value masked, in its values and its keys alike.

    As written and URL-encoded, a short value only as a whole word, as a vendor's answer is masked, so data is
    not mangled; base64-encoded or ``\\u``-escaped only a value long enough not to match ordinary text.
    """
    secrets = configured_secrets()
    if not secrets:
        return value
    plain = answer_masker(secrets)
    encoded = encoded_pattern([secret for secret in secrets if len(secret) >= VENDOR_MASK_INSIDE_MIN])

    def text(item: str) -> str:
        return mask_encoded(plain(item), encoded, VENDOR_MASK)

    return map_strings(value, text, keys=True)


def render_masked(payload: Any) -> str:
    """``payload`` as the tool returns it, every configured credential masked.

    A result can carry a failure's text too -- an extract's per-URL error, a job's status detail -- and an object
    renders as its ``str``, so the rendered JSON is what gets masked.
    """
    text = render(payload)
    return render(mask_configured(json.loads(text))) if configured_secrets() else text
