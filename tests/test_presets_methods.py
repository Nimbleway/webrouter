"""Presets on each method: jobs and handles pinning theirs, refusals naming them, the agent tools.

Every router method takes ``preset=``, and these run each one over the in-memory
fakes in conftest, so nothing here touches the network or needs a key. A fake's
``requests`` list is the evidence that a call did, or did not, reach the backend.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import FakeExtractProvider
from tests.presets_helpers import REF, NarrowProvider, SlowResearch, definition
from web_router import (
    AsyncWebRouter,
    Preset,
    UnsupportedParameterError,
    WebRouter,
)


def test_extract_takes_a_preset(fake_extract_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(definition(components={"extract": {"defaults": {"format": "text"}}}))

    response = router.extract("https://example.com", preset="acme.research@1.0.0")

    assert response.documents[0].format == "text"
    assert response.meta is not None and response.meta.preset is not None
    assert response.meta.preset.component == "extract"


def test_run_template_takes_a_preset_for_its_format(fake_template_provider: Any) -> None:
    router = WebRouter()
    router.presets.add(definition(components={"templates": {"defaults": {"format": "markdown"}}}))

    response = router.run_template(
        "books_pdp", {"url": "https://example.com"}, provider="fake", preset="acme.research@1.0.0"
    )

    assert router.get_template_provider("fake").requests[0].format == "markdown"  # type: ignore[attr-defined]
    assert response.meta is not None


def test_answer_takes_a_preset(fake_answer_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(definition(components={"answer": {"defaults": {"recency": "week", "country": "US"}}}))

    response = router.answer("q", preset="acme.research@1.0.0")

    request = router.get_answer_provider("fake").requests[0]  # type: ignore[attr-defined]
    assert (request.recency, request.country) == ("week", "US")
    assert response.meta is not None


def test_research_takes_a_preset(fake_research_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(definition(components={"research": {"defaults": {"effort": "low"}}}))

    response = router.research("q", preset="acme.research@1.0.0")

    assert router.get_research_provider("fake").requests[0].effort == "low"  # type: ignore[attr-defined]
    assert response.meta is not None and response.meta.preset is not None
    assert response.meta.preset.validation.state == "passed"


def test_a_submitted_job_pins_its_preset_and_reads_pending_until_it_finishes(clock: Any) -> None:
    from web_router.registry import register_research_provider, unregister_research_provider

    register_research_provider("fake", SlowResearch)
    try:
        router = WebRouter("fake")
        router.presets.add(definition(components={"research": {"defaults": {"effort": "low"}}}))

        job = router.submit_research("q", preset="acme.research@1.0.0")

        assert job.meta is not None and job.meta.preset is not None
        assert job.meta.preset.validation.state == "pending"
        response = job.result()
        assert job.meta.preset.validation.state == "passed"
        assert response.meta is not None and response.meta.preset is not None
        assert response.meta.preset.effective == {"effort": "low"}
    finally:
        unregister_research_provider("fake")


def test_a_job_without_a_preset_carries_no_meta(fake_research_provider: Any) -> None:
    job = WebRouter("fake").submit_research("q")
    assert job.meta is None
    assert job.result().meta is None


def test_browse_and_submit_browse_take_a_preset(fake_browse_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(definition(components={"browse": {"defaults": {"max_steps": 4}}}))

    response = router.browse("t", preset="acme.research@1.0.0")
    job = router.submit_browse("t", preset="acme.research@1.0.0")

    assert [request.max_steps for request in router.get_browse_provider("fake").requests] == [4, 4]  # type: ignore[attr-defined]
    assert response.meta is not None
    assert job.result().meta is not None


def test_open_browser_pins_its_preset_on_the_handle(fake_browser_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(
        definition(components={"browser": {"defaults": {"timeout": 120, "viewport": {"width": 800, "height": 600}}}})
    )

    with router.open_browser(preset="acme.research@1.0.0") as handle:
        request = router.get_browser_provider("fake").requests[0]  # type: ignore[attr-defined]
        assert request.timeout == 120
        assert request.viewport is not None and request.viewport.width == 800
        assert handle.meta is not None and handle.meta.preset is not None
        assert handle.meta.preset.effective["viewport"] == {"width": 800, "height": 600}


async def test_the_async_router_applies_presets_the_same_way(fake_provider: Any) -> None:
    async with AsyncWebRouter("fake", preset=Preset.model_validate(definition())) as router:
        response = await router.search("q")

    assert response.meta is not None and response.meta.preset is not None
    assert response.meta.preset.resolved[0].source == "client"


def test_changing_pinned_metadata_does_not_change_what_a_job_reports(fake_research_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(definition(components={"research": {"defaults": {"effort": "low"}}}))
    job = router.submit_research("q", preset="acme.research@1.0.0")

    assert job.meta is not None and job.meta.preset is not None
    job.meta.preset.effective["effort"] = "high"
    job.meta.preset.resolved.clear()

    result = job.result()
    assert result.meta is not None and result.meta.preset is not None
    assert result.meta.preset.effective == {"effort": "low"}
    assert len(result.meta.preset.resolved) == 1


def test_a_job_polled_with_status_carries_its_preset_on_the_answer(clock: Any) -> None:
    from web_router.registry import register_research_provider, unregister_research_provider

    register_research_provider("fake", SlowResearch)
    try:
        router = WebRouter("fake")
        router.presets.add(definition(components={"research": {"defaults": {"effort": "low"}}}))
        job = router.submit_research("q", preset="acme.research@1.0.0")

        progress = job.status()
        while not progress.is_terminal:
            progress = job.status()

        assert progress.response is not None and progress.response.meta is not None
        assert job.progress.response is not None and job.progress.response.meta is not None
    finally:
        unregister_research_provider("fake")


def test_changing_what_status_returned_does_not_change_the_next_read(fake_research_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(definition(components={"research": {"defaults": {"effort": "low"}}}))
    job = router.submit_research("q", preset=REF)

    first = job.status()
    assert first.response is not None and first.response.meta is not None and first.response.meta.preset is not None
    first.response.meta.preset.effective["effort"] = "high"

    again = job.status()
    assert again.response is not None and again.response.meta is not None and again.response.meta.preset is not None
    assert again.response.meta.preset.effective == {"effort": "low"}


def test_the_agent_tools_take_the_router_presets_backends(
    fake_answer_provider: Any, fake_extract_provider: Any
) -> None:
    from web_router.integrations._options import answer_provider_for, extract_provider_for

    sections = {"answer": {"defaults": {"provider": "fake"}}, "extract": {"defaults": {"provider": "fake"}}}
    router = WebRouter("brave", preset=Preset.model_validate(definition(components=sections)))

    assert answer_provider_for(None, None, router) == "fake"
    assert extract_provider_for(None, None, router) is None  # each call resolves the router's preset backend
    assert answer_provider_for(None, "linkup", router) == "linkup"


def test_a_locked_router_preset_backend_wins_over_the_search_provider(
    fake_answer_provider: Any, fake_extract_provider: Any
) -> None:
    """``exa`` searches, extracts and answers; sent in place of a locked preset's backend, every call would conflict."""
    from web_router.integrations._tools import WebRouterTools

    sections = {"answer": {"defaults": {"provider": "fake"}}, "extract": {"defaults": {"provider": "fake"}}}
    router = WebRouter("brave", preset=Preset.model_validate(definition(components=sections)))
    tools: WebRouterTools[Any] = WebRouterTools(router, methods=("extract", "answer"), provider="exa")

    assert tools.extract_provider is None and tools.answer_provider == "fake"
    extracted = router.extract("https://example.com", provider=tools.extract_provider)
    answered = router.answer("q", provider=tools.answer_provider)
    assert extracted.provider == "fake" and answered.provider == "fake"


async def test_the_extract_tool_leaves_each_router_its_own_preset(fake_extract_provider: Any) -> None:
    pytest.importorskip("langchain_core")
    from web_router.integrations.langchain import web_router_tools
    from web_router.registry import register_extract_provider, unregister_extract_provider

    class OtherExtract(FakeExtractProvider):
        name = "other"

    register_extract_provider("other", OtherExtract)
    try:
        sync_preset = definition("acme.sync", components={"extract": {"defaults": {"provider": "fake"}}})
        async_preset = definition("acme.async", components={"extract": {"defaults": {"provider": "other"}}})
        router = WebRouter("brave", preset=Preset.model_validate(sync_preset))
        async_router = AsyncWebRouter("brave", preset=Preset.model_validate(async_preset))
        _, extract_tool = web_router_tools(router, async_router=async_router)

        extract_tool._run(["https://example.com"])
        await extract_tool._arun(["https://example.com"])

        assert router.get_extract_provider("fake").calls == [["https://example.com"]]  # type: ignore[attr-defined]
        assert async_router.get_extract_provider("other").calls == [["https://example.com"]]  # type: ignore[attr-defined]
        await async_router.aclose()
        router.close()
    finally:
        unregister_extract_provider("other")


def test_a_backends_refusal_of_a_preset_setting_names_the_preset(narrow_provider: Any) -> None:
    router = WebRouter("narrow")
    router.presets.add(definition(components={"search": {"defaults": {"country": "US"}}}))

    with pytest.raises(UnsupportedParameterError) as caught:
        router.search("q", preset=REF)

    assert caught.value.parameters == ["country"]
    assert REF in str(caught.value)


def test_a_preset_setting_the_backend_does_not_map_is_refused_not_dropped(narrow_provider: Any) -> None:
    router = WebRouter("narrow")
    router.presets.add(definition(components={"search": {"defaults": {"country": "US"}}}))

    with pytest.raises(UnsupportedParameterError, match="country"):
        router.search("q", preset="acme.research@1.0.0")
    narrow = router.get_provider("narrow")
    assert isinstance(narrow, NarrowProvider) and narrow.requests == []
