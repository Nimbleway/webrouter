"""Presets on every method at once: every setting reaches the request, and every refusal sends nothing.

Every router method takes ``preset=``, and these run each one over the in-memory
fakes in conftest, so nothing here touches the network or needs a key. A fake's
``requests`` list is the evidence that a call did, or did not, reach the backend.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from tests.presets_helpers import REF, RecordingExtract, definition
from web_router import (
    AsyncWebRouter,
    Preset,
    PresetValidationError,
    Viewport,
    WebRouter,
)
from web_router.constants_presets import PRESET_FIELDS

ARGUMENTS: dict[str, dict[str, Any]] = {
    "search": {
        "max_results": 3,
        "search_depth": "deep",
        "include_domains": ["a.com"],
        "exclude_domains": ["b.com"],
        "country": "US",
        "locale": "en",
        "provider_options": {"x": 1},
    },
    "extract": {"format": "text", "provider_options": {"x": 1}},
    "templates": {"format": "html", "provider_options": {"x": 1}},
    "research": {
        "output_schema": {"type": "object"},
        "effort": "high",
        "include_domains": ["a.com"],
        "exclude_domains": ["b.com"],
        "provider_options": {"x": 1},
    },
    "browse": {
        "start_url": "https://example.com",
        "output_schema": {"type": "object"},
        "max_steps": 5,
        "allowed_domains": ["a.com"],
        "provider_options": {"x": 1},
    },
    "browser": {
        "proxy_country": "US",
        "stealth": "high",
        "solve_captchas": True,
        "profile": "p1",
        "persist_profile": True,
        "timeout": 60,
        "idle_timeout": 30,
        "viewport": {"width": 800, "height": 600},
        "headless": True,
        "provider_options": {"x": 1},
    },
    "answer": {
        "include_domains": ["a.com"],
        "exclude_domains": ["b.com"],
        "country": "US",
        "locale": "en",
        "recency": "week",
        "provider_options": {"x": 1},
    },
}
"""A valid value for every setting each method's presets can hold, but ``provider``."""


GETTERS = {
    "search": "get_provider",
    "extract": "get_extract_provider",
    "templates": "get_template_provider",
    "research": "get_research_provider",
    "browse": "get_browse_provider",
    "browser": "get_browser_provider",
    "answer": "get_answer_provider",
}


SYNC_CALLS: list[tuple[str, Callable[..., object]]] = [
    ("search", lambda router, **kw: router.search("q", **kw)),
    ("extract", lambda router, **kw: router.extract("https://example.com", **kw)),
    ("templates", lambda router, **kw: router.run_template("books_pdp", {"url": "u"}, provider="fake", **kw)),
    ("answer", lambda router, **kw: router.answer("q", **kw)),
    ("research", lambda router, **kw: router.research("q", **kw)),
    ("research", lambda router, **kw: router.submit_research("q", **kw)),
    ("browse", lambda router, **kw: router.browse("t", **kw)),
    ("browse", lambda router, **kw: router.submit_browse("t", **kw)),
    ("browser", lambda router, **kw: router.open_browser(**kw)),
]


ASYNC_CALLS: list[tuple[str, Callable[..., Awaitable[object]]]] = [
    ("search", lambda router, **kw: router.search("q", **kw)),
    ("extract", lambda router, **kw: router.extract("https://example.com", **kw)),
    ("templates", lambda router, **kw: router.run_template("books_pdp", {"url": "u"}, provider="fake", **kw)),
    ("answer", lambda router, **kw: router.answer("q", **kw)),
    ("research", lambda router, **kw: router.research("q", **kw)),
    ("research", lambda router, **kw: router.submit_research("q", **kw)),
    ("browse", lambda router, **kw: router.browse("t", **kw)),
    ("browse", lambda router, **kw: router.submit_browse("t", **kw)),
    ("browser", lambda router, **kw: router.open_browser(**kw)),
]


ALL_FAKES = (
    "fake_provider",
    "fake_extract_provider",
    "fake_template_provider",
    "fake_answer_provider",
    "fake_research_provider",
    "fake_browse_provider",
    "fake_browser_provider",
)


@pytest.fixture
def every_fake(request: pytest.FixtureRequest) -> None:
    from web_router.registry import register_extract_provider

    for name in ALL_FAKES:
        request.getfixturevalue(name)
    register_extract_provider("fake", RecordingExtract)  # the fixture's teardown unregisters it


def _setup(component: str, source: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The preset section and the call's arguments: every setting from one source, the other left to the preset."""
    if source == "call":
        # A preset still applies, so the call goes through resolution with every argument set.
        section = {"defaults": {"provider_options": {"from": "preset"}}, "overridable": ["provider_options"]}
        return section, dict(ARGUMENTS[component])
    return {"defaults": dict(ARGUMENTS[component])}, {}


def _assert_reached(router: Any, component: str) -> None:
    request = getattr(router, GETTERS[component])("fake").requests[-1]
    for name, value in ARGUMENTS[component].items():
        expected = Viewport(**value) if name == "viewport" else value
        assert getattr(request, name) == expected, name


def test_the_argument_table_covers_every_setting_a_preset_can_hold() -> None:
    assert {component: set(values) | {"provider"} for component, values in ARGUMENTS.items()} == {
        component: set(fields) | {"provider"} for component, fields in PRESET_FIELDS.items()
    }


@pytest.mark.parametrize("source", ["call", "preset"])
@pytest.mark.parametrize(("component", "call"), SYNC_CALLS)
def test_every_setting_reaches_the_request_from_the_call_or_a_preset(
    component: str, call: Callable[..., object], source: str, every_fake: None
) -> None:
    """Every argument each method takes goes through resolution and arrives; none is dropped on the way."""
    section, arguments = _setup(component, source)
    with WebRouter("fake") as router:
        router.presets.add(definition(components={component: section}))
        call(router, preset=REF, **arguments)
        _assert_reached(router, component)


@pytest.mark.parametrize("source", ["call", "preset"])
@pytest.mark.parametrize(("component", "call"), ASYNC_CALLS)
async def test_every_setting_reaches_the_request_on_the_async_router(
    component: str, call: Callable[..., Awaitable[object]], source: str, every_fake: None
) -> None:
    section, arguments = _setup(component, source)
    async with AsyncWebRouter("fake") as router:
        router.presets.add(definition(components={component: section}))
        await call(router, preset=REF, **arguments)
        _assert_reached(router, component)


ALL_SECTIONS = {
    "extract": {"defaults": {"format": "text"}},
    "templates": {"defaults": {"format": "markdown"}},
    "answer": {"defaults": {"country": "US"}},
    "research": {"defaults": {"effort": "low"}},
    "browse": {"defaults": {"max_steps": 4}},
    "browser": {"defaults": {"timeout": 120}},
}


async def test_every_async_method_applies_its_preset_and_pins_it(
    fake_extract_provider: Any,
    fake_template_provider: Any,
    fake_answer_provider: Any,
    fake_research_provider: Any,
    fake_browse_provider: Any,
    fake_browser_provider: Any,
) -> None:
    ref = "acme.research@1.0.0"
    async with AsyncWebRouter("fake") as router:
        router.presets.add(definition(components=ALL_SECTIONS))

        extracted = await router.extract("https://example.com", preset=ref)
        templated = await router.run_template("books_pdp", {"url": "u"}, provider="fake", preset=ref)
        answered = await router.answer("q", preset=ref)
        researched = await router.research("q", preset=ref)
        research_job = await router.submit_research("q", preset=ref)
        browsed = await router.browse("t", preset=ref)
        browse_job = await router.submit_browse("t", preset=ref)
        handle = await router.open_browser(preset=ref)
        async with handle:
            assert handle.meta is not None and handle.meta.preset is not None
            assert handle.meta.preset.effective == {"timeout": 120}

        assert extracted.documents[0].format == "text"
        assert router.get_template_provider("fake").requests[0].format == "markdown"  # type: ignore[attr-defined]
        assert router.get_answer_provider("fake").requests[0].country == "US"  # type: ignore[attr-defined]
        assert [request.effort for request in router.get_research_provider("fake").requests] == ["low", "low"]  # type: ignore[attr-defined]
        assert [request.max_steps for request in router.get_browse_provider("fake").requests] == [4, 4]  # type: ignore[attr-defined]
        assert router.get_browser_provider("fake").requests[0].timeout == 120  # type: ignore[attr-defined]

        finished = [await research_job.aresult(), await browse_job.aresult()]
        for response in (extracted, templated, answered, researched, browsed, *finished):
            assert response.meta is not None and response.meta.preset is not None
            assert response.meta.preset.validation.state == "passed"


LOCK_REFUSALS = [
    ("fake_answer_provider", "answer", {"country": "US"}, lambda r: r.answer("q", country="DE", preset=REF), "answer"),
    (
        "fake_template_provider",
        "templates",
        {"format": "markdown"},
        lambda r: r.run_template("books_pdp", {"url": "u"}, provider="fake", format="html", preset=REF),
        "template",
    ),
    (
        "fake_research_provider",
        "research",
        {"effort": "low"},
        lambda r: r.research("q", effort="high", preset=REF),
        "research",
    ),
    (
        "fake_research_provider",
        "research",
        {"effort": "low"},
        lambda r: r.submit_research("q", effort="high", preset=REF),
        "research",
    ),
    ("fake_browse_provider", "browse", {"max_steps": 4}, lambda r: r.browse("t", max_steps=9, preset=REF), "browse"),
    (
        "fake_browse_provider",
        "browse",
        {"max_steps": 4},
        lambda r: r.submit_browse("t", max_steps=9, preset=REF),
        "browse",
    ),
    ("fake_browser_provider", "browser", {"timeout": 120}, lambda r: r.open_browser(timeout=60, preset=REF), "browser"),
]


@pytest.mark.parametrize(("fixture", "component", "defaults", "call", "getter"), LOCK_REFUSALS)
def test_a_refused_call_sends_nothing_for_any_method(
    fixture: str,
    component: str,
    defaults: dict[str, Any],
    call: Callable[[WebRouter], object],
    getter: str,
    request: pytest.FixtureRequest,
) -> None:
    request.getfixturevalue(fixture)
    router = WebRouter("fake")
    router.presets.add(definition(components={component: {"defaults": defaults}}))

    with pytest.raises(PresetValidationError):
        call(router)

    backend = getattr(router, f"get_{getter}_provider")("fake")
    assert backend.requests == []


def test_no_method_reports_a_preset_when_none_applied(
    fake_extract_provider: Any,
    fake_template_provider: Any,
    fake_answer_provider: Any,
    fake_research_provider: Any,
    fake_browse_provider: Any,
    fake_browser_provider: Any,
) -> None:
    router = WebRouter("fake", preset=Preset.model_validate(definition()))  # a search preset, so none of these

    assert router.extract("https://example.com").meta is None
    assert router.run_template("books_pdp", {"url": "u"}, provider="fake").meta is None
    assert router.answer("q").meta is None
    assert router.research("q").meta is None
    assert router.browse("t").meta is None
    assert router.submit_browse("t").meta is None
    with router.open_browser() as handle:
        assert handle.meta is None
