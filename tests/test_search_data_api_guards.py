"""Data API matching never costs a search its web results, and never blocks an async caller's event loop."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Any

import pytest

from web_router import AsyncWebRouter, WebRouter, data_api_discovery
from web_router.vendors.catalog import get_catalog


def test_a_matching_failure_keeps_the_paid_web_results(
    fake_provider: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise RuntimeError("matching bug")

    monkeypatch.setattr(data_api_discovery, "find_matches", broken)
    with caplog.at_level(logging.WARNING), WebRouter("fake") as router:
        response = router.search("datadog monitors")
    assert response.urls == ["https://example.com"]
    assert response.data_apis == []
    assert "matching failed" in caplog.text


def test_a_catalog_that_fails_with_an_os_error_is_skipped(
    fake_provider: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def unreadable() -> Any:
        raise PermissionError("catalog directory")

    monkeypatch.setattr(data_api_discovery, "get_catalog", unreadable)
    with caplog.at_level(logging.WARNING), WebRouter("fake") as router:
        response = router.search("datadog monitors")
    assert response.urls == ["https://example.com"]
    assert response.data_apis == []
    assert "catalog directory" in caplog.text


async def test_an_async_search_reads_the_catalog_off_the_event_loop(
    fake_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop_thread = threading.get_ident()
    first_read: list[int] = []

    def recording() -> Any:
        first_read.append(threading.get_ident())
        return get_catalog()

    monkeypatch.setattr(data_api_discovery, "get_catalog", recording)
    async with AsyncWebRouter("fake") as router:
        response = await router.search("datadog monitors")
    assert response.data_apis
    assert first_read[0] != loop_thread


class _VaultDown:
    """A credentials source whose backing store times out on every read."""

    def get(self, vendor: str, name: str, env: Sequence[str]) -> str | None:
        raise TimeoutError("vault timed out")


def test_a_credentials_source_that_throws_keeps_the_paid_web_results(
    fake_provider: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING), WebRouter("fake", vendor_credentials=_VaultDown()) as router:
        response = router.search("datadog monitors")
    assert response.urls == ["https://example.com"]
    assert response.data_apis == []
    assert "matching failed" in caplog.text
