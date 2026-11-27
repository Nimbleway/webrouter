"""A GitHub URL resolves through the public router and the existing vendor caller."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from web_router import AsyncWebRouter, InvalidParameterError, ProviderError, WebRouter
from web_router.vendors.caller import VendorCaller

BASE = "https://github.com/Nimbleway/webrouter"
API = "/repos/Nimbleway/webrouter"


@pytest.fixture
def requests(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    seen: list[httpx.Request] = []
    responses = {
        API: httpx.Response(200, json={"full_name": "Nimbleway/webrouter"}),
        f"{API}/readme": httpx.Response(200, json={"encoding": "base64", "content": "SGVs\nbG8h\n"}),
        f"{API}/issues/11": httpx.Response(200, json={"number": 11, "body": "Issue"}),
        f"{API}/issues/11/comments": httpx.Response(200, json=[]),
        f"{API}/pulls/12": httpx.Response(200, json={"number": 12, "mergeable": True}),
        f"{API}/issues/12/comments": httpx.Response(
            200,
            json=[{"body": "Review discussion"}],
            headers={"Link": '<https://api.github.com/example?page=2>; rel="next"'},
        ),
        "/repos/example/empty": httpx.Response(200, json={"name": "empty"}),
        "/repos/example/empty/readme": httpx.Response(404, json={"message": "Not Found"}),
        "/repos/example/limited": httpx.Response(200, json={"name": "limited"}),
        "/repos/example/limited/readme": httpx.Response(429, json={"message": "Rate Limited"}),
    }

    def reply(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert request.method == "GET"
        seen.append(request)
        return responses[request.url.path]

    transport = httpx.MockTransport(reply)

    def caller(router: WebRouter | AsyncWebRouter) -> VendorCaller:
        if router._vendor_caller is None:
            router._vendor_caller = VendorCaller(
                router.vendor_credential_source(), transport=transport, async_transport=transport
            )
        return router._vendor_caller

    monkeypatch.setattr(WebRouter, "_caller", caller)
    monkeypatch.setattr(AsyncWebRouter, "_caller", caller)
    yield seen


def test_repository_and_missing_readme(requests: list[httpx.Request]) -> None:
    with WebRouter() as router:
        result = router.github_read(BASE)
        assert (result.url, result.kind, result.resource["full_name"], result.readme) == (
            BASE,
            "repository",
            "Nimbleway/webrouter",
            "Hello!",
        )
        assert router.github_read("https://github.com/example/empty").readme is None
        with pytest.raises(ProviderError, match="429"):
            router.github_read("https://github.com/example/limited")
    assert [request.url.path for request in requests[:2]] == [API, f"{API}/readme"]
    assert all("authorization" not in request.headers for request in requests)


@pytest.mark.asyncio
async def test_issue_and_pull_request_context(requests: list[httpx.Request]) -> None:
    async with AsyncWebRouter(data_api_credentials={"github": {"token": "test-token"}}) as router:
        result = await router.github_read(f"{BASE}/issues/11#issuecomment-23")
        assert result.kind == "issue" and result.resource["body"] == "Issue"
        assert result.comments == [] and not result.comments_truncated
        result = await router.github_read(f"{BASE}/pull/12")
        assert result.kind == "pull_request" and result.resource["mergeable"] is True
        assert result.comments == [{"body": "Review discussion"}] and result.comments_truncated
    assert [request.url.path for request in requests] == [
        f"{API}/issues/11",
        f"{API}/issues/11/comments",
        f"{API}/pulls/12",
        f"{API}/issues/12/comments",
    ]
    assert requests[-1].url.params["per_page"] == "100"
    assert all(request.headers["authorization"] == "Bearer test-token" for request in requests)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com.evil.example/Nimbleway/webrouter",
        "https://example.com@github.com/Nimbleway/webrouter",
        "https://github.com/Nimbleway/../issues/123",
        "https://github.com/Nimbleway/webrouter/blob/main/README.md",
        "https://github.com/Nimbleway/webrouter/issues/0",
    ],
)
def test_rejects_other_url_shapes(url: str, requests: list[httpx.Request]) -> None:
    with WebRouter() as router, pytest.raises(InvalidParameterError, match=r"github\.com"):
        router.github_read(url)
    assert not requests
