"""Clearing every variable that configures a built-in live data backend.

A backend can have more than one credential set (Oxylabs: its Web API's key,
or its Web Scraper API's username and password), so this reads
``LIVE_DATA_CREDENTIAL_SETS`` rather than ``LIVE_DATA_ENV_BY_PROVIDER`` alone.
"""

from __future__ import annotations

import pytest

from web_router.constants_live_data import LIVE_DATA_CREDENTIAL_SETS

LIVE_DATA_CREDENTIAL_ENV_VARS = sorted(
    {env_var for sets in LIVE_DATA_CREDENTIAL_SETS.values() for env_vars in sets for env_var in env_vars}
)
"""Every variable in any live data backend's credential sets."""


def clear_live_data_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset every live data credential, so no built-in backend is configured from the environment."""
    for env_var in LIVE_DATA_CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
