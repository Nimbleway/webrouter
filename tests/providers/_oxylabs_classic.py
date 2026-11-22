"""What the Oxylabs Web Scraper API ("Classic") live data tests share: its answers and a wired provider.

Shapes follow https://developers.oxylabs.io/products/web-scraper-api/targets,
read on 2026-11-17, and the answers seen live that day: a parsed page under
``results[].content`` with ``parse_status_code`` 12000, and 12003 with no
fields for a source Oxylabs does not parse. Values here are synthetic.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.providers._live_data_http import Api, wire
from web_router.providers.oxylabs_classic import OxylabsClassicLiveDataProvider

BASE = "https://realtime.oxylabs.io"
PAGE = {"title": "A thing", "price": 9.99, "parse_status_code": 12000}
ENV = ("OXYLABS_USERNAME", "OXYLABS_PASSWORD", "OXYLABS_WEB_API_KEY")


def parsed(*contents: Any, status: int = 200) -> dict[str, Any]:
    results = [
        {"content": content, "status_code": status, "url": "https://www.example.com/p/1", "job_id": "1", "page": 1}
        for content in contents or (PAGE,)
    ]
    return {"results": results, "job": {"id": "7398271642069149697", "status": "done", "source": "amazon_search"}}


def classic(answer: dict[str, Any] | None = None, status: int = 200) -> tuple[OxylabsClassicLiveDataProvider, Api]:
    api = Api(lambda request: httpx.Response(status, json=answer if answer is not None else parsed()))
    provider = OxylabsClassicLiveDataProvider(username="user", api_key="password")
    return wire(provider, api), api  # type: ignore[return-value]


@pytest.fixture(name="no_oxylabs_env", autouse=True)
def no_oxylabs_env_fixture(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("integration"):
        return  # a live test runs on the credentials the environment holds
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
