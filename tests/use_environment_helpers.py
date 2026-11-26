"""What the ``use_environment`` tests share: every backend variable set, or none, and the key passed in code."""

from __future__ import annotations

import pytest

from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    BROWSE_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    ENV_AGENTCORE_GATEWAY_URL,
    ENV_BY_PROVIDER,
    EXTRACT_ENV_BY_PROVIDER,
    PROVIDER_EXA,
    RESEARCH_ENV_BY_PROVIDER,
)
from web_router.constants_litellm import LITELLM_SEARCH_SETTINGS_BY_PROVIDER
from web_router.constants_live_data import LIVE_DATA_CREDENTIAL_SETS

_TABLES = (ANSWER_ENV_BY_PROVIDER, BROWSE_ENV_BY_PROVIDER, BROWSER_ENV_BY_PROVIDER, EXTRACT_ENV_BY_PROVIDER)
EVERY_VARIABLE = sorted(
    {var for table in (*_TABLES, RESEARCH_ENV_BY_PROVIDER) for names in table.values() for var in names}
    | {var for var in ENV_BY_PROVIDER.values() if var}
    | {var for names in LITELLM_SEARCH_SETTINGS_BY_PROVIDER.values() for var in names}
    | {var for sets in LIVE_DATA_CREDENTIAL_SETS.values() for names in sets for var in names}
    | {ENV_AGENTCORE_GATEWAY_URL}
)
"""Every variable a backend reads its key or setting from."""

ENV_KEY = "key-from-the-environment"
MINE = {PROVIDER_EXA: {"api_key": "key-passed-in-code"}}
"""The ``provider_config`` the tests pass: one backend keyed in code."""


def every_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """The launch profile's environment: a value in every backend variable."""
    for var in EVERY_VARIABLE:
        monkeypatch.setenv(var, ENV_KEY if "URL" not in var and "BASE" not in var else "https://gateway.example.com")


def no_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """An environment holding no backend variable at all."""
    for var in EVERY_VARIABLE:
        monkeypatch.delenv(var, raising=False)
