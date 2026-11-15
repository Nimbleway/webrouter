"""Whether a LiteLLM proxy is usable, read before any provider is built: what ``auto`` checks in ``provider_config``.

Kept apart from :mod:`web_router.providers.litellm` so the chain can ask without importing the vendors' modules,
and so the proxy URL has one rule, used here and by the providers.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from web_router.constants_litellm import ENV_LITELLM_PROXY_API_BASE, ENV_LITELLM_PROXY_API_KEY
from web_router.exceptions import ConfigurationError
from web_router.routing.remote_url import checked_url


def safe_proxy_url(url: str) -> str | None:
    """``url`` as the client will use it, or ``None`` when the ``remote`` router's rule refuses it: ``https``, or
    ``http`` to loopback, with no credentials, query or fragment, since the proxy key rides on every request."""
    import httpx  # the search extra's, which every LiteLLM provider needs; imported only once a proxy is set

    try:
        return str(checked_url(url, httpx)).rstrip("/")
    except ConfigurationError:
        return None


def ready_in_code(config: Mapping[str, Any]) -> bool:
    """Whether ``provider_config`` makes a LiteLLM search provider usable: a safe proxy URL and a key, each from
    ``config`` or else the environment. ``auto`` charges a member's estimate before building it, so one that
    cannot start must not join."""
    key = config.get("api_key") or (os.environ.get(ENV_LITELLM_PROXY_API_KEY) or "").strip()
    url = config.get("base_url") or os.environ.get(ENV_LITELLM_PROXY_API_BASE) or ""
    return bool(key and isinstance(url, str) and url.strip() and safe_proxy_url(url.strip()))
