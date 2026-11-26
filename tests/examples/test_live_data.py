"""examples/live_data.py's key guard: it runs on Nimble alone, so another vendor's key exits, no network."""

from __future__ import annotations

import sys

import pytest

from tests.examples.helpers import load_example
from tests.live_data_env_helpers import clear_live_data_credentials
from web_router.constants_live_data import ENV_APIFY_TOKEN


def test_another_vendors_key_alone_says_to_set_nimbles(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_live_data_credentials(monkeypatch)
    monkeypatch.setenv(ENV_APIFY_TOKEN, "x")
    try:
        with pytest.raises(SystemExit, match="NIMBLE_API_KEY"):
            load_example("live_data")
    finally:
        sys.modules.pop("live_data", None)
