"""Fixtures shared by the tests of a search over every live data backend.

Two in-memory backends stand in for the vendors: ``fake`` (``tests/_live_data_fake.py``)
and ``paged`` (``tests/_live_data_paged.py``), both registered as custom
backends, which are always tried. Every built-in backend's variable is
cleared, so no test reaches a real vendor. A test module imports both
fixtures; ``no_vendor_keys`` applies itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests._live_data_fake import FakeLiveDataProvider
from tests._live_data_paged import PagedProvider
from tests.live_data_env_helpers import clear_live_data_credentials
from web_router.registry import register_live_data_provider, unregister_live_data_provider


@pytest.fixture(name="no_vendor_keys", autouse=True)
def no_vendor_keys_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_live_data_credentials(monkeypatch)


@pytest.fixture(name="two_backends")
def two_backends_fixture(fake_live_data_provider: Any) -> Iterator[None]:
    register_live_data_provider("paged", PagedProvider)
    yield
    unregister_live_data_provider("paged")
    FakeLiveDataProvider.list_raises = None
