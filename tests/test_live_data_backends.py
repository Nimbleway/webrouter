"""Which live data backends a router can use when no provider is named, and why one is left out.

``fake`` (``tests/_live_data_fake.py``) stands in for a working vendor. Nimble's
SDK is made missing by blocking ``nimble_python`` and dropping the provider
modules already imported, so the registry imports them afresh and fails as it
would without the extra. Every built-in backend's variable is cleared first.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from web_router import WebRouter
from web_router.constants_live_data import LIVE_DATA_ENV_BY_PROVIDER


@pytest.fixture(autouse=True)
def no_vendor_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_vars in LIVE_DATA_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)


@pytest.fixture
def no_nimble_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in [name for name in sys.modules if name.startswith("web_router.providers.nimble")]:
        monkeypatch.delitem(sys.modules, module)
    monkeypatch.setitem(sys.modules, "nimble_python", None)


def test_a_backend_whose_sdk_is_missing_is_skipped_when_it_has_no_key(
    fake_live_data_provider: Any, no_nimble_sdk: None
) -> None:
    router = WebRouter()

    page = router.live_data_sources()
    result = router.live_data("books_pdp", {"url": "https://example.com/p/1"})

    assert {source.provider for source in page.sources} == {"fake"} and page.failures == {}
    assert result.provider == "fake"


def test_a_keyed_backend_whose_sdk_is_missing_is_named_in_failures(
    fake_live_data_provider: Any, no_nimble_sdk: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")

    page = WebRouter().live_data_sources()

    assert {source.provider for source in page.sources} == {"fake"}
    assert "nimble" in page.failures
