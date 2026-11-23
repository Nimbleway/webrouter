"""A registry owns the live custom-registration lookup for each method."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import FakeBrowserProvider
from tests.routing_helpers import TOOLS
from web_router import registry, registry_answer
from web_router.providers.base import LiveDataProvider

METHODS = [(t.name, t.register, t.unregister, t.fake) for t in TOOLS] + [
    ("open_browser", registry.register_browser_provider, registry.unregister_browser_provider, FakeBrowserProvider),
    ("live_data", registry.register_live_data_provider, registry.unregister_live_data_provider, LiveDataProvider),
]


@pytest.mark.parametrize(("method", "register", "unregister", "provider"), METHODS, ids=[row[0] for row in METHODS])
def test_custom_registration_lookup_is_live_and_method_specific(
    method: str, register: Any, unregister: Any, provider: Any
) -> None:
    name = "replacement"
    assert not registry.is_custom(method, name)
    register(name, provider)
    try:
        assert registry.is_custom(method, name)
        assert not registry.is_custom("unknown", name)
        assert all(not registry.is_custom(other, name) for other, *_ in METHODS if other != method)
        assert registry_answer.is_custom(name) is (method == "answer")
    finally:
        unregister(name)
    assert not registry.is_custom(method, name)
