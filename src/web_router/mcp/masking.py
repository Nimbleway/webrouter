"""Masking every configured credential's value out of what the server hands a model.

:func:`~web_router.mcp.redaction.redact` finds a secret by where it sits -- a
field named for one, a key-bearing URL -- so a backend that echoes a key as
``invalid key <KEY>`` or ``Bearer <KEY>`` in plain text would slip past it.
This masks by value instead: every credential set in this process, wherever
it appears, as :func:`~web_router.vendors.masking.mask_answer` masks a
vendor's own.
"""

from __future__ import annotations

import os
from typing import Any

from web_router.constants import ENV_AGENTCORE_GATEWAY_URL
from web_router.mcp.env_credentials import OPTIONAL_KEYS, credential_variables, free_variables
from web_router.vendors.masking import mask, mask_answer

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
    return mask(text, configured_secrets())


def mask_configured(value: Any) -> Any:
    """A copy of a payload with every configured credential's value masked, as written and URL-encoded.

    A short value only as a whole word, as a vendor's answer is masked, so data is not mangled.
    """
    secrets = configured_secrets()
    return mask_answer(value, secrets) if secrets else value
