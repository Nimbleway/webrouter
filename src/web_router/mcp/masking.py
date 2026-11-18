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

from web_router.mcp.env_credentials import OPTIONAL_KEYS, credential_variables, free_variables
from web_router.vendors.masking import mask_answer


def configured_secrets() -> list[str]:
    """The value of every credential variable set here, less a vendor's settings -- a region like ``us5`` is no secret.

    An optional key, such as Jina's, bills nothing but is still a key, so it is masked.
    """
    names = credential_variables() - (free_variables() - set(OPTIONAL_KEYS))
    return [value for name in sorted(names) if (value := os.environ.get(name))]


def mask_configured(value: Any) -> Any:
    """A copy of ``value`` with every configured credential's value masked, as written and URL-encoded."""
    secrets = configured_secrets()
    return mask_answer(value, secrets) if secrets else value
