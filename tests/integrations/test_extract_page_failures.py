"""An extract whose every URL failed carries each URL's own failure, and the tools show each page's kind."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.conftest import FakeExtractProvider
from web_router import AllProvidersFailedError, ExtractFailedError, ProviderError, WebRouter
from web_router.integrations import anthropic, openai
from web_router.integrations._plan import Planned, failed
from web_router.registry import register_extract_provider, unregister_extract_provider
from web_router.types import ExtractFailure, ExtractRequest, ExtractResponse

BLOCKED = "https://blocked.example/"
GONE = "https://gone.example/"
# One URL a challenge page kept back, which another try may get; one the site says is gone, which none will.
EXPECTED = [(BLOCKED, "upstream", True, None), (GONE, "target", False, 404)]


class MixedExtractProvider(FakeExtractProvider):
    name = "mixed"

    def _respond(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        failures = [
            ExtractFailure(url=BLOCKED, error="served a challenge page", kind="upstream"),
            ExtractFailure(url=GONE, error="HTTP 404", status_code=404),
        ]
        return ExtractResponse(provider=self.name, failures=[f for f in failures if f.url in urls])


@pytest.fixture(autouse=True)
def mixed() -> Iterator[None]:
    register_extract_provider(MixedExtractProvider.name, MixedExtractProvider)
    yield
    unregister_extract_provider(MixedExtractProvider.name)


def _rows(content: str) -> list[tuple[Any, ...]]:
    shown = json.loads(content)
    return [(row["url"], row["kind"], row["retryable"], row.get("status_code")) for row in shown["failures"]]


def test_the_raised_error_carries_each_urls_own_failure_and_is_still_a_provider_error() -> None:
    with pytest.raises(ExtractFailedError) as caught:
        WebRouter().extract([BLOCKED, GONE], provider="mixed")

    error = caught.value
    assert isinstance(error, ProviderError) and error.kind == "upstream" and error.retryable
    assert [(f.url, f.kind, f.retryable, f.status_code) for f in error.url_failures] == EXPECTED


@pytest.mark.parametrize("module", [anthropic, openai])
def test_the_claude_and_openai_tools_list_each_pages_kind(module: Any) -> None:
    tools = module.WebRouterTools(WebRouter(), extract_provider="mixed", url_policy=None)
    result = tools.call("web_router_extract", {"urls": [BLOCKED, GONE]})

    assert result.is_error and isinstance(result.error, ExtractFailedError)
    assert _rows(result.content) == EXPECTED


def test_the_langchain_tool_lists_each_pages_kind() -> None:
    pytest.importorskip("langchain_core")
    from web_router.integrations.langchain import WebRouterExtract

    tool = WebRouterExtract(router=WebRouter(), provider="mixed", url_policy=None)
    message = tool.invoke({"type": "tool_call", "id": "c", "name": tool.name, "args": {"urls": [BLOCKED, GONE]}})

    assert message.status == "error" and _rows(message.content) == EXPECTED


def test_under_auto_each_url_keeps_the_latest_members_own_failure() -> None:
    earlier = ExtractFailedError("a", "x", [ExtractFailure(url=BLOCKED, error="HTTP 404", status_code=404)])
    latest = ExtractFailedError("b", "x", [ExtractFailure(url=BLOCKED, error="challenge page", kind="upstream")])
    gone = ExtractFailedError("c", "x", [ExtractFailure(url=GONE, error="HTTP 404", status_code=404)])
    refused = ProviderError("d", "HTTP 401 Unauthorized", kind="auth")
    members = {"a": earlier, "b": latest, "c": gone, "d": refused}
    error = AllProvidersFailedError("auto", "every backend failed", members)

    shown = failed(Planned("web_router_extract", "extract", urls=[BLOCKED, GONE, "https://other.example/"]), error, 500)

    assert _rows(shown) == [*EXPECTED, ("https://other.example/", "upstream", True, None)]
