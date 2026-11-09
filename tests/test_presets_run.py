"""preset= on every method: the preset's params, the call's own arguments on top, the method's normal response."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from web_router import (
    AsyncBrowserHandle,
    AsyncWebRouter,
    BrowseJob,
    BrowserHandle,
    InMemoryPresetStore,
    Preset,
    PresetApplied,
    PresetNotFoundError,
    PresetOfAnotherToolError,
    ResearchJob,
    WebRouter,
)

FAKES = (
    "fake_provider",
    "fake_extract_provider",
    "fake_answer_provider",
    "fake_research_provider",
    "fake_browse_provider",
    "fake_template_provider",
    "fake_browser_provider",
)


def _store() -> InMemoryPresetStore:
    return InMemoryPresetStore(
        [
            Preset(name="news", tool="search", params={"max_results": 3, "search_depth": "fast", "country": "US"}),
            Preset(name="page", tool="extract", params={"format": "text"}),
            Preset(name="ask", tool="answer", params={"recency": "week"}),
            Preset(name="dig", tool="research", params={"effort": "low", "timeout": 30}),
            Preset(name="task", tool="browse", params={"start_url": "https://example.com"}),
            Preset(name="book", tool="run_template", params={"format": "markdown"}),
            Preset(
                name="browser", tool="open_browser", params={"stealth": "low", "viewport": {"width": 8, "height": 6}}
            ),
        ]
    )


@pytest.fixture
def router(request: pytest.FixtureRequest) -> WebRouter:
    for fixture in FAKES:
        request.getfixturevalue(fixture)
    return WebRouter("fake", presets=_store())


def test_a_search_with_a_preset_sends_its_params_and_says_so(router: WebRouter) -> None:
    response = router.search("nvidia earnings", preset="news")

    request = router.get_provider().requests[0]  # type: ignore[attr-defined]
    assert (request.query, request.max_results, request.search_depth, request.country) == (
        "nvidia earnings",
        3,
        "fast",
        "US",
    )
    assert response.preset == PresetApplied(
        name="news", params={"max_results": 3, "search_depth": "fast", "country": "US"}
    )


def test_the_calls_own_argument_overrides_the_presets(router: WebRouter) -> None:
    response = router.search("q", preset="news", max_results=7)

    assert router.get_provider().requests[0].max_results == 7  # type: ignore[attr-defined]
    assert response.preset is not None and response.preset.params["max_results"] == 7


def test_a_call_without_a_preset_carries_none(router: WebRouter) -> None:
    assert router.search("q").preset is None


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda r: r.extract("https://example.com", preset="page"), {"format": "text"}),
        (lambda r: r.answer("who won", preset="ask"), {"recency": "week"}),
        (lambda r: r.research("how", preset="dig"), {"effort": "low", "timeout": 30.0}),
        (lambda r: r.browse("find it", preset="task"), {"start_url": "https://example.com"}),
        (
            lambda r: r.run_template("books_pdp", {"url": "https://books.example/1"}, provider="fake", preset="book"),
            {"format": "markdown", "inputs": {"url": "https://books.example/1"}},
        ),
    ],
)
def test_each_method_returns_its_own_response_with_the_preset_on_it(
    router: WebRouter, call: Any, expected: dict[str, Any]
) -> None:
    with router:
        response = call(router)

    assert response.preset.params == expected


def test_a_research_preset_reaches_the_request(router: WebRouter) -> None:
    router.research("how", preset="dig")

    assert router.get_research_provider().requests[0].effort == "low"  # type: ignore[attr-defined]


def test_submit_uses_the_preset_of_the_method_it_starts_without_its_wait(router: WebRouter) -> None:
    research = router.submit_research("how", preset="dig")
    browse = router.submit_browse("find it", preset="task")

    assert isinstance(research, ResearchJob) and isinstance(browse, BrowseJob)
    assert router.get_research_provider().requests[0].effort == "low"  # type: ignore[attr-defined]
    assert router.get_browse_provider().requests[0].start_url == "https://example.com"  # type: ignore[attr-defined]


def test_an_open_browser_preset_opens_a_tracked_session_with_its_settings(router: WebRouter) -> None:
    handle = router.open_browser(preset="browser")

    assert isinstance(handle, BrowserHandle)
    provider = router.get_browser_provider()
    assert provider.requests[0].viewport.width == 8  # type: ignore[attr-defined]
    assert provider.requests[0].stealth == "low"  # type: ignore[attr-defined]
    router.close()
    assert provider.released == [handle.session_id]  # type: ignore[attr-defined]


def test_a_preset_of_another_method_is_refused_before_anything_is_sent(router: WebRouter) -> None:
    with pytest.raises(PresetOfAnotherToolError, match="Preset 'page' runs extract"):
        router.search("q", preset="page")

    assert router.get_provider().requests == []  # type: ignore[attr-defined]


def test_an_unknown_preset_is_refused_by_name(router: WebRouter) -> None:
    with pytest.raises(PresetNotFoundError, match="No preset named 'nope'"):
        router.search("q", preset="nope")


def test_a_merge_the_method_would_refuse_raises_as_a_direct_call_does(router: WebRouter) -> None:
    with pytest.raises(ValidationError) as refused:
        router.search("q", preset="news", max_results=0)

    assert [error["loc"] for error in refused.value.errors()] == [("max_results",)]
    assert router.get_provider().requests == []  # type: ignore[attr-defined]


def test_a_template_preset_cannot_name_the_provider() -> None:
    with pytest.raises(ValidationError):
        Preset(name="book", tool="run_template", params={"provider": "fake"})


def test_a_router_given_no_store_starts_with_an_empty_one_of_its_own() -> None:
    first, second = WebRouter(), WebRouter()

    assert isinstance(first.presets, InMemoryPresetStore)
    assert first.presets.list() == []
    assert first.presets is not second.presets


@pytest.fixture
def arouter(request: pytest.FixtureRequest) -> AsyncWebRouter:
    for fixture in FAKES:
        request.getfixturevalue(fixture)
    return AsyncWebRouter("fake", presets=_store())


async def test_the_async_router_applies_a_preset_the_same_way(arouter: AsyncWebRouter) -> None:
    async with arouter:
        response = await arouter.search("nvidia earnings", preset="news", max_results=4)
        research = await arouter.research("how", preset="dig")
        answer = await arouter.answer("who won", preset="ask")

    assert response.preset == PresetApplied(
        name="news", params={"max_results": 4, "search_depth": "fast", "country": "US"}
    )
    assert research.preset is not None and research.preset.name == "dig"
    assert answer.preset is not None and answer.preset.params == {"recency": "week"}


async def test_the_async_router_opens_and_tracks_a_preset_session(arouter: AsyncWebRouter) -> None:
    handle = await arouter.open_browser(preset="browser")

    assert isinstance(handle, AsyncBrowserHandle)
    provider = arouter.get_browser_provider()
    await arouter.aclose()
    assert provider.released == [handle.session_id]  # type: ignore[attr-defined]


async def test_the_async_router_refuses_a_preset_of_another_method(arouter: AsyncWebRouter) -> None:
    with pytest.raises(PresetOfAnotherToolError):
        await arouter.extract("https://example.com", preset="news")
