"""A parent's private-network opt-out never disables the child binary's guard."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.providers._obscura_fake import calls, make_fake
from web_router import AsyncWebRouter, WebRouter

URLS = ["https://doi.org/10.1038/nphys1170", "http://localhost:8080", "http://169.254.169.254/latest/meta-data/"]


@pytest.fixture(params=[None, "1"])
def inherited(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str | None:
    value: str | None = request.param
    if value is None:
        monkeypatch.delenv("OBSCURA_ALLOW_PRIVATE_NETWORK", raising=False)
    else:
        monkeypatch.setenv("OBSCURA_ALLOW_PRIVATE_NETWORK", value)
    monkeypatch.setenv("FAKE_OBSCURA", "page")
    return value


def test_sync_fetch_keeps_the_private_network_guard(tmp_path: Path, inherited: str | None) -> None:
    fake = make_fake(tmp_path)
    with WebRouter("obscura", provider_config={"obscura": {"executable_path": str(fake)}}) as router:
        response = router.extract(URLS)
    assert [d.url for d in response.documents] == URLS
    assert [run["allow_private_network"] for run in calls(fake)] == [None] * len(URLS)
    assert os.environ.get("OBSCURA_ALLOW_PRIVATE_NETWORK") == inherited


async def test_async_fetch_keeps_the_private_network_guard(tmp_path: Path, inherited: str | None) -> None:
    fake = make_fake(tmp_path)
    async with AsyncWebRouter("obscura", provider_config={"obscura": {"executable_path": str(fake)}}) as router:
        response = await router.extract(URLS)
    assert [d.url for d in response.documents] == URLS
    assert [run["allow_private_network"] for run in calls(fake)] == [None] * len(URLS)
    assert os.environ.get("OBSCURA_ALLOW_PRIVATE_NETWORK") == inherited
