"""Presets: loading, precedence, locks and that every refusal comes before a backend is called.

Every router method takes ``preset=``, and these run each one over the in-memory
fakes in conftest, so nothing here touches the network or needs a key. A fake's
``requests`` list is the evidence that a call did, or did not, reach the backend.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from tests.conftest import FakeProvider, FakeResearchProvider
from web_router import (
    AsyncWebRouter,
    Preset,
    PresetCompatibilityError,
    PresetError,
    PresetNotFoundError,
    PresetRegistry,
    PresetValidationError,
    UnsupportedParameterError,
    WebRouter,
)
from web_router.constants_presets import PRESET_FIELDS
from web_router.registry import register_provider, unregister_provider
from web_router.types import NORMALIZED_PARAMETERS


def definition(
    preset_id: str = "acme.research",
    version: str = "1.0.0",
    components: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    components = components or {"search": {"defaults": {"max_results": 5}}}
    return {
        "spec_version": 1,
        "id": preset_id,
        "version": version,
        "applies_to": list(components),
        "components": components,
        **extra,
    }


def searched(router: WebRouter) -> FakeProvider:
    provider = router.get_provider("fake")
    assert isinstance(provider, FakeProvider)
    return provider


class NarrowProvider(FakeProvider):
    """A search backend that maps ``max_results`` and nothing else, as most real ones map few options."""

    name = "narrow"
    supported_parameters = frozenset({"max_results"})


@pytest.fixture
def narrow_provider() -> Any:
    register_provider("narrow", NarrowProvider)
    yield NarrowProvider
    unregister_provider("narrow")


def test_presets_load_from_json_and_yaml_files_and_directories(tmp_path: Path) -> None:
    (tmp_path / "search.json").write_text(json.dumps(definition("acme.search")))
    (tmp_path / "extract.yaml").write_text(
        "spec_version: 1\nid: acme.extract\nversion: 2.1.0\napplies_to: [extract]\n"
        "components:\n  extract:\n    defaults: {format: text}\n"
    )
    (tmp_path / "notes.txt").write_text("not a preset")
    registry = PresetRegistry()

    loaded = registry.load(tmp_path)

    assert [preset.ref for preset in loaded] == ["acme.extract@2.1.0", "acme.search@1.0.0"]
    assert registry.get("acme.extract@2.1.0").section("extract").defaults == {"format": "text"}
    assert [preset.ref for preset in registry.list(component="search")] == ["acme.search@1.0.0"]


def test_a_file_can_hold_several_definitions(tmp_path: Path) -> None:
    path = tmp_path / "presets.json"
    path.write_text(json.dumps([definition(version="1.0.0"), definition(version="1.1.0")]))
    registry = PresetRegistry()

    registry.load(path)

    assert [preset.version for preset in registry.list()] == ["1.0.0", "1.1.0"]


def test_a_lookup_needs_an_exact_version_and_names_the_ones_loaded() -> None:
    registry = PresetRegistry()
    registry.add(definition(version="1.0.0"))
    registry.add(definition(version="1.2.0"))

    with pytest.raises(PresetValidationError, match=re.escape("namespace.name@major.minor.patch")):
        registry.get("acme.research")
    with pytest.raises(PresetNotFoundError) as caught:
        registry.get("acme.research@2.0.0")
    assert caught.value.versions == ["1.0.0", "1.2.0"]


def test_a_loaded_version_never_changes() -> None:
    registry = PresetRegistry()
    registry.add(definition())
    registry.add(definition())  # the same definition again is a no-op

    with pytest.raises(PresetValidationError, match="new version"):
        registry.add(definition(components={"search": {"defaults": {"max_results": 9}}}))
    assert registry.get("acme.research@1.0.0").section("search").defaults == {"max_results": 5}


def test_a_bad_file_leaves_the_registry_as_it_was(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps(definition("acme.good")))
    (tmp_path / "b.json").write_text(
        json.dumps(definition("acme.bad", components={"search": {"defaults": {"nope": 1}}}))
    )
    registry = PresetRegistry()

    with pytest.raises(PresetValidationError):
        registry.load(tmp_path)
    assert len(registry) == 0


def test_yaml_without_pyyaml_names_the_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "preset.yaml"
    path.write_text("spec_version: 1\n")
    monkeypatch.setitem(sys.modules, "yaml", None)

    with pytest.raises(PresetError, match="pyyaml"):
        PresetRegistry().load(path)


@pytest.mark.parametrize(
    ("components", "field"),
    [
        ({"search": {"defaults": {"query": "fixed"}}}, "components.search.defaults.query"),
        ({"research": {"defaults": {"timeout": 60}}}, "components.research.defaults.timeout"),
        ({"research": {"defaults": {"records": []}}}, "components.research.defaults.records"),
        ({"templates": {"defaults": {"template": "books_pdp"}}}, "components.templates.defaults.template"),
        ({"search": {"defaults": {"max_results": 0}}}, "components.search.defaults.max_results"),
        ({"extract": {"defaults": {"format": "pdf"}}}, "components.extract.defaults.format"),
        ({"search": {"defaults": {"provider": "oxylabs"}}}, "components.search.defaults.provider"),
        ({"search": {"defaults": {}, "overridable": ["max_results"]}}, "components.search.overridable"),
    ],
)
def test_a_setting_its_method_cannot_take_fails_at_load(components: dict[str, Any], field: str) -> None:
    with pytest.raises(PresetValidationError) as caught:
        PresetRegistry().add(definition(components=components))
    assert [issue.field for issue in caught.value.issues] == [field]


def test_a_section_must_be_listed_in_applies_to() -> None:
    malformed = definition()
    malformed["applies_to"] = ["extract"]
    with pytest.raises(PresetValidationError) as caught:
        PresetRegistry().add(malformed)
    assert caught.value.issues[0].field == "components.search"


@pytest.mark.parametrize(
    "change",
    [{"spec_version": 2}, {"id": "nonamespace"}, {"version": "latest"}, {"shared": {"policy_refs": []}}],
)
def test_the_manifest_refuses_what_this_library_cannot_honor(change: dict[str, Any]) -> None:
    with pytest.raises(PresetValidationError):
        PresetRegistry().add({**definition(), **change})


def test_every_component_can_hold_provider_options_and_none_holds_its_native_input() -> None:
    assert all("provider_options" in fields for fields in PRESET_FIELDS.values())
    assert "query" not in PRESET_FIELDS["search"]
    assert "urls" not in PRESET_FIELDS["extract"]
    assert "task" not in PRESET_FIELDS["browse"]
    assert PRESET_FIELDS["search"] == NORMALIZED_PARAMETERS | {"provider", "provider_options"}


def test_a_call_with_no_preset_is_unchanged(fake_provider: Any) -> None:
    router = WebRouter("fake")

    response = router.search("q", max_results=3)

    assert response.meta is None
    assert searched(router).requests[0].max_results == 3


def test_a_per_call_preset_fills_what_the_call_left_unset(fake_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(definition(components={"search": {"defaults": {"max_results": 5, "country": "US"}}}))

    response = router.search("q", preset="acme.research@1.0.0")

    request = searched(router).requests[0]
    assert (request.max_results, request.country) == (5, "US")
    assert response.meta is not None and response.meta.preset is not None
    assert response.meta.preset.effective == {"max_results": 5, "country": "US"}
    assert response.meta.preset.validation.state == "passed"
    assert [entry.ref for entry in response.meta.preset.resolved] == ["acme.research@1.0.0"]


def test_explicit_argument_beats_per_call_preset_beats_client_preset(fake_provider: Any) -> None:
    client = definition(
        "acme.client",
        components={"search": {"defaults": {"max_results": 9, "locale": "fr"}, "overridable": ["max_results"]}},
    )
    call = definition(
        "acme.call",
        components={"search": {"defaults": {"max_results": 5, "country": "US"}, "overridable": ["country"]}},
    )
    router = WebRouter("fake", preset=Preset.model_validate(client))
    router.presets.add(call)

    response = router.search("q", country="DE", preset="acme.call@1.0.0")

    request = searched(router).requests[0]
    assert (request.max_results, request.country, request.locale) == (5, "DE", "fr")
    assert response.meta is not None and response.meta.preset is not None
    meta = response.meta.preset
    assert [(entry.source, entry.ref) for entry in meta.resolved] == [
        ("request", "acme.call@1.0.0"),
        ("client", "acme.client@1.0.0"),
    ]
    assert meta.effective == {"max_results": 5, "locale": "fr"}
    assert meta.overridden == ["country"]


def test_a_client_preset_reference_can_be_loaded_after_the_router_is_built(fake_provider: Any) -> None:
    router = WebRouter("fake", preset="acme.research@1.0.0")
    router.presets.add(definition())

    router.search("q")

    assert searched(router).requests[0].max_results == 5


def test_a_preset_can_choose_the_backend(fake_provider: Any) -> None:
    router = WebRouter("auto")
    router.presets.add(definition(components={"search": {"defaults": {"provider": "fake"}}}))

    response = router.search("q", preset="acme.research@1.0.0")

    assert response.provider == "fake"


def test_a_malformed_client_reference_fails_when_the_router_is_built() -> None:
    with pytest.raises(PresetValidationError):
        WebRouter("auto", preset="acme.research")


LOCKED = definition(
    components={"search": {"defaults": {"max_results": 5, "country": "US"}, "overridable": ["country"]}}
)


def test_replacing_a_locked_setting_raises_before_the_backend_is_called(fake_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(LOCKED)

    with pytest.raises(PresetValidationError) as caught:
        router.search("q", max_results=3, preset="acme.research@1.0.0")

    assert [issue.field for issue in caught.value.issues] == ["max_results"]
    assert searched(router).requests == []


def test_an_overridable_setting_and_an_equal_value_are_both_allowed(fake_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(LOCKED)

    router.search("q", max_results=5, country="DE", preset="acme.research@1.0.0")

    request = searched(router).requests[0]
    assert (request.max_results, request.country) == (5, "DE")


def test_a_per_call_preset_cannot_replace_what_the_client_preset_locked(fake_provider: Any) -> None:
    router = WebRouter("fake", preset=Preset.model_validate(LOCKED))
    router.presets.add(definition("acme.other", components={"search": {"defaults": {"max_results": 9}}}))

    with pytest.raises(PresetValidationError, match=re.escape("acme.research@1.0.0")):
        router.search("q", preset="acme.other@1.0.0")
    assert searched(router).requests == []


def test_a_lock_error_never_repeats_the_callers_value(fake_provider: Any) -> None:
    locked = definition(components={"search": {"defaults": {"provider_options": {"a": 1}}, "overridable": []}})
    router = WebRouter("fake")
    router.presets.add(locked)

    with pytest.raises(PresetValidationError) as caught:
        router.search("q", provider_options={"api_key": "sk-secret"}, preset="acme.research@1.0.0")
    assert "sk-secret" not in str(caught.value)


def test_a_per_call_preset_for_another_method_is_refused(fake_extract_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(definition())

    with pytest.raises(PresetCompatibilityError):
        router.extract("https://example.com", preset="acme.research@1.0.0")
    assert router.get_extract_provider("fake").calls == []  # type: ignore[attr-defined]


def test_a_client_preset_for_another_method_simply_does_not_apply(fake_extract_provider: Any) -> None:
    router = WebRouter("fake", preset=Preset.model_validate(definition()))

    response = router.extract("https://example.com")

    assert response.meta is None


def test_an_unknown_preset_is_refused_before_the_backend_is_called(fake_provider: Any) -> None:
    router = WebRouter("fake")

    with pytest.raises(PresetNotFoundError):
        router.search("q", preset="acme.missing@1.0.0")
    assert searched(router).requests == []


def test_a_preset_setting_the_backend_does_not_map_is_refused_not_dropped(narrow_provider: Any) -> None:
    router = WebRouter("narrow")
    router.presets.add(definition(components={"search": {"defaults": {"country": "US"}}}))

    with pytest.raises(UnsupportedParameterError, match="country"):
        router.search("q", preset="acme.research@1.0.0")
    narrow = router.get_provider("narrow")
    assert isinstance(narrow, NarrowProvider) and narrow.requests == []


def test_validate_reports_what_would_stop_the_call_without_making_it(narrow_provider: Any) -> None:
    router = WebRouter("narrow")
    router.presets.add(definition(components={"search": {"defaults": {"max_results": 5, "country": "US"}}}))

    check = router.presets.validate("search", {"query": "q", "bogus": 1}, preset="acme.research@1.0.0")

    assert not check.ok
    assert check.provider == "narrow"
    assert {issue.field for issue in check.issues} == {"bogus", "country"}
    assert check.effective == {"max_results": 5, "country": "US"}
    assert len(router.presets) == 1
    assert router._instances == {}  # no backend was even built


def test_validate_passes_a_call_the_backend_can_serve(fake_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(
        definition(components={"search": {"defaults": {"max_results": 5}, "overridable": ["max_results"]}})
    )

    check = router.presets.validate("search", {"query": "q", "max_results": 3}, preset="acme.research@1.0.0")

    assert check.ok
    assert check.overridden == ["max_results"]


def test_validate_turns_a_preset_error_into_an_issue() -> None:
    check = PresetRegistry().validate("search", preset="acme.missing@1.0.0")
    assert not check.ok
    assert check.issues[0].field == "preset"


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


class SlowResearch(FakeResearchProvider):
    polls_before_done = 1


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


def test_leaving_overridable_out_locks_every_default(fake_provider: Any) -> None:
    """The spec's rule: only the defaults a section declares editable may change."""
    router = WebRouter("fake")
    router.presets.add(definition())

    with pytest.raises(PresetValidationError, match="max_results"):
        router.search("q", max_results=3, preset="acme.research@1.0.0")
    assert searched(router).requests == []


def test_a_preset_above_cannot_release_a_lock_by_repeating_its_value(fake_provider: Any) -> None:
    client = definition("acme.client", components={"search": {"defaults": {"max_results": 3}}})
    call = definition(
        "acme.call", components={"search": {"defaults": {"max_results": 3}, "overridable": ["max_results"]}}
    )
    router = WebRouter("fake", preset=Preset.model_validate(client))
    router.presets.add(call)

    with pytest.raises(PresetValidationError, match=re.escape("acme.client@1.0.0")):
        router.search("q", max_results=10, preset="acme.call@1.0.0")
    assert searched(router).requests == []


class _Answer(BaseModel):
    name: str


@pytest.mark.parametrize(
    ("component", "name", "held", "given"),
    [
        ("search", "provider", "fake", "FAKE"),
        ("search", "include_domains", ["a.com"], ("a.com",)),
        ("research", "output_schema", _Answer.model_json_schema(), _Answer),
    ],
)
def test_the_same_setting_written_another_way_is_not_a_conflict(
    component: str, name: str, held: Any, given: Any, fake_provider: Any
) -> None:
    registry = PresetRegistry()
    registry.add(definition(components={component: {"defaults": {name: held}}}))

    resolution = registry.resolve(component, "acme.research@1.0.0", **{name: given})  # type: ignore[arg-type]

    assert resolution.overridden == []


def test_one_load_refuses_two_definitions_under_one_reference(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps(definition("acme.same")))
    (tmp_path / "b.json").write_text(
        json.dumps(definition("acme.same", components={"search": {"defaults": {"max_results": 9}}}))
    )
    registry = PresetRegistry()

    with pytest.raises(PresetValidationError, match="more than once"):
        registry.load(tmp_path)
    assert len(registry) == 0


def test_nothing_handed_out_can_change_a_loaded_preset(fake_provider: Any) -> None:
    router = WebRouter("fake")
    source = definition()
    added = router.presets.add(source)

    source["components"]["search"]["defaults"]["max_results"] = 40
    added.components["search"].defaults["max_results"] = 50
    router.presets.get("acme.research@1.0.0").components["search"].defaults["max_results"] = 60
    router.presets.list()[0].components["search"].overridable.append("max_results")
    router.search("q", preset="acme.research@1.0.0")

    assert searched(router).requests[0].max_results == 5
    with pytest.raises(PresetValidationError):
        router.search("q", max_results=3, preset="acme.research@1.0.0")


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


def test_a_backends_own_options_appear_by_their_keys_alone(fake_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(definition(components={"search": {"defaults": {"provider_options": {"api_key": "sk-x"}}}}))

    response = router.search("q", preset="acme.research@1.0.0")
    with pytest.raises(PresetValidationError) as caught:
        router.search("q", provider_options={"other": 1}, preset="acme.research@1.0.0")

    assert response.meta is not None and response.meta.preset is not None
    assert response.meta.preset.effective == {"provider_options": ["api_key"]}
    assert searched(router).requests[0].provider_options == {"api_key": "sk-x"}
    assert "sk-x" not in str(caught.value)


def test_a_definition_passed_as_a_reference_is_never_repeated_in_the_error(fake_provider: Any) -> None:
    secret = definition(components={"search": {"defaults": {"provider_options": {"api_key": "sk-x"}}}})

    with pytest.raises(PresetValidationError) as caught:
        WebRouter("fake").search("q", preset=secret)  # type: ignore[arg-type]
    assert "sk-x" not in str(caught.value)


def test_a_reference_with_a_trailing_newline_is_refused() -> None:
    with pytest.raises(PresetValidationError):
        WebRouter("auto", preset="acme.research@1.0.0\n")


def test_a_null_default_is_refused_at_load() -> None:
    with pytest.raises(PresetValidationError, match="null"):
        PresetRegistry().add(definition(components={"search": {"defaults": {"country": None}}}))


def test_validate_takes_a_jobs_timeout_as_the_method_does(fake_research_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(definition(components={"research": {"defaults": {"effort": "low"}}}))

    check = router.presets.validate("research", {"query": "q", "timeout": 60}, preset="acme.research@1.0.0")

    assert check.ok


class _Stop(Exception):
    """Raised by a stand-in resolver, so a test can see what a method sent it without building a backend."""


SYNC_CALLS: list[tuple[str, Callable[[WebRouter], object]]] = [
    ("search", lambda router: router.search("q")),
    ("extract", lambda router: router.extract("https://example.com")),
    ("templates", lambda router: router.run_template("books_pdp", provider="fake")),
    ("answer", lambda router: router.answer("q")),
    ("research", lambda router: router.research("q")),
    ("research", lambda router: router.submit_research("q")),
    ("browse", lambda router: router.browse("t")),
    ("browse", lambda router: router.submit_browse("t")),
    ("browser", lambda router: router.open_browser()),
]

ASYNC_CALLS: list[tuple[str, Callable[[AsyncWebRouter], Awaitable[object]]]] = [
    ("search", lambda router: router.search("q")),
    ("extract", lambda router: router.extract("https://example.com")),
    ("templates", lambda router: router.run_template("books_pdp", provider="fake")),
    ("answer", lambda router: router.answer("q")),
    ("research", lambda router: router.research("q")),
    ("research", lambda router: router.submit_research("q")),
    ("browse", lambda router: router.browse("t")),
    ("browse", lambda router: router.submit_browse("t")),
    ("browser", lambda router: router.open_browser()),
]


def _capturing(seen: dict[str, Any]) -> Callable[..., Any]:
    def resolve(component: str, preset: Any, **explicit: Any) -> Any:
        seen.update(component=component, names=set(explicit))
        raise _Stop

    return resolve


@pytest.mark.parametrize(("component", "call"), SYNC_CALLS)
def test_every_argument_a_method_takes_goes_through_its_presets(
    component: str, call: Callable[[WebRouter], object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A setting left out of the resolver would be dropped from the request, the one failure presets must not add."""
    router = WebRouter()
    seen: dict[str, Any] = {}
    monkeypatch.setattr(router.presets, "resolve", _capturing(seen))

    with pytest.raises(_Stop):
        call(router)

    assert seen == {"component": component, "names": PRESET_FIELDS[component]}  # type: ignore[index]


@pytest.mark.parametrize(("component", "call"), ASYNC_CALLS)
async def test_every_argument_an_async_method_takes_goes_through_its_presets(
    component: str, call: Callable[[AsyncWebRouter], Awaitable[object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    router = AsyncWebRouter()
    seen: dict[str, Any] = {}
    monkeypatch.setattr(router.presets, "resolve", _capturing(seen))

    with pytest.raises(_Stop):
        await call(router)

    assert seen == {"component": component, "names": PRESET_FIELDS[component]}  # type: ignore[index]


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
REF = "acme.research@1.0.0"


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


def test_a_yaml_parse_error_never_quotes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "preset.yaml"
    path.write_text("spec_version: 1\nprovider_options: {api_key: sk-SECRET-123: oops}\n")

    with pytest.raises(PresetError) as caught:
        PresetRegistry().load(path)

    assert "sk-SECRET-123" not in str(caught.value)
    assert caught.value.__cause__ is None and "line 2" in str(caught.value)


def test_a_bad_provider_options_value_is_never_repeated() -> None:
    bad = definition(components={"search": {"defaults": {"provider_options": ["api_key", "sk-SECRET-789"]}}})

    with pytest.raises(PresetValidationError) as caught:
        PresetRegistry().add(bad)

    assert "sk-SECRET-789" not in str(caught.value)


def test_a_request_never_shares_a_nested_value_with_the_registry(fake_provider: Any) -> None:
    router = WebRouter("fake")
    nested = {"search": {"defaults": {"provider_options": {"filters": {"lang": "en"}}}}}
    router.presets.add(definition(components=nested))

    router.search("q", preset=REF)
    searched(router).requests[0].provider_options["filters"]["lang"] = "fr"
    router.search("q", preset=REF)

    assert searched(router).requests[1].provider_options == {"filters": {"lang": "en"}}
    assert router.presets.get(REF).section("search").defaults["provider_options"] == {"filters": {"lang": "en"}}


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


def test_validate_picks_the_answer_backend_the_call_would(
    fake_answer_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web_router.constants import ANSWER_ENV_BY_PROVIDER

    for env_vars in ANSWER_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)
    router = WebRouter("tavily", provider_config={"fake": {"api_key": "k"}})
    router.presets.add(definition(components={"answer": {"defaults": {"country": "US"}}}))

    check = router.presets.validate("answer", {"query": "q"}, preset=REF)

    assert check.ok and check.provider == "fake"
    assert router.answer("q", preset=REF).provider == "fake"


def test_a_backends_refusal_of_a_preset_setting_names_the_preset(narrow_provider: Any) -> None:
    router = WebRouter("narrow")
    router.presets.add(definition(components={"search": {"defaults": {"country": "US"}}}))

    with pytest.raises(UnsupportedParameterError) as caught:
        router.search("q", preset=REF)

    assert caught.value.parameters == ["country"]
    assert REF in str(caught.value)


def test_a_yaml_preset_holds_the_strings_a_json_one_would(tmp_path: Path, fake_provider: Any) -> None:
    path = tmp_path / "preset.yaml"
    path.write_text(
        "spec_version: 1\nid: acme.research\nversion: 1.0.0\napplies_to: [search]\n"
        "components:\n  search:\n    defaults:\n      provider_options: {startPublishedDate: 2025-01-01}\n"
    )
    router = WebRouter("fake")
    router.presets.load(path)

    router.search("q", preset=REF)

    assert searched(router).requests[0].provider_options == {"startPublishedDate": "2025-01-01"}


@pytest.mark.parametrize(
    "value",
    ["!!timestamp 2024-02-30", "!!int sk-SECRET-1", "!!binary aGVsbG8=", "!!set {a: null}"],
)
def test_a_yaml_value_json_cannot_hold_is_a_preset_error_that_quotes_nothing(tmp_path: Path, value: str) -> None:
    path = tmp_path / "preset.yaml"
    path.write_text(
        "spec_version: 1\nid: acme.research\nversion: 1.0.0\napplies_to: [search]\n"
        f"components:\n  search:\n    defaults:\n      provider_options: {{value: {value}}}\n"
    )

    with pytest.raises(PresetError) as caught:
        PresetRegistry().load(path)

    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_a_file_that_is_not_utf8_carries_no_cause(tmp_path: Path) -> None:
    path = tmp_path / "preset.json"
    path.write_bytes('{"title": "café", "provider_options": {"api_key": "sk-SECRET-2"}}'.encode("latin-1"))

    with pytest.raises(PresetError) as caught:
        PresetRegistry().load(path)

    assert caught.value.__cause__ is None
    assert "SECRET" not in str(caught.value)


def test_a_one_shot_iterable_from_the_caller_reaches_the_request_whole(fake_provider: Any) -> None:
    router = WebRouter("fake")
    domains = {"search": {"defaults": {"include_domains": ["a.com"]}, "overridable": ["include_domains"]}}
    router.presets.add(definition(components=domains))

    router.search("q", include_domains=iter(["b.com", "c.com"]), preset=REF)  # type: ignore[arg-type]

    assert searched(router).requests[0].include_domains == ["b.com", "c.com"]


@pytest.mark.parametrize(
    "given",
    [
        json.dumps(definition(components={"search": {"defaults": {"provider_options": {"api_key": "sk-x"}}}})),
        "acme.research@1.0",
        "Summer.2026_pass",
    ],
)
def test_a_string_that_is_not_a_reference_is_never_repeated(given: str) -> None:
    with pytest.raises(PresetValidationError) as caught:
        WebRouter("auto", preset=given)

    assert given not in str(caught.value) and "sk-x" not in str(caught.value)
    assert "namespace.name@major.minor.patch" in str(caught.value)


def test_validate_checks_the_callers_own_values_without_repeating_them(fake_provider: Any) -> None:
    check = PresetRegistry(default_provider="fake").validate("search", {"query": "q", "max_results": 0})

    assert not check.ok
    assert [issue.field for issue in check.issues] == ["max_results"]


def test_validate_reads_the_preset_from_the_request_as_the_method_takes_it(fake_provider: Any) -> None:
    registry = PresetRegistry(default_provider="fake")
    registry.add(definition())

    from_request = registry.validate("search", {"query": "q", "preset": REF})
    twice = registry.validate("search", {"query": "q", "preset": REF}, preset=REF)

    assert from_request.ok and [entry.ref for entry in from_request.resolved] == [REF]
    assert [issue.field for issue in twice.issues] == ["preset"]


@pytest.mark.parametrize("provider", [["fake"], 5, {"name": "fake"}])
def test_validate_reports_a_provider_of_the_wrong_kind_instead_of_crashing(provider: Any, fake_provider: Any) -> None:
    check = PresetRegistry(default_provider="fake").validate("search", {"query": "q", "provider": provider})

    assert not check.ok
    assert [issue.field for issue in check.issues] == ["provider"]


def test_validate_reads_a_one_shot_iterable_once_as_the_call_would(fake_provider: Any) -> None:
    registry = PresetRegistry(default_provider="fake")
    registry.add(definition(components={"search": {"defaults": {"include_domains": ["a.com"]}}}))

    check = registry.validate("search", {"query": "q", "include_domains": iter(["a.com"])}, preset=REF)

    assert check.ok and check.overridden == []


def test_a_key_passed_as_a_reference_is_never_repeated() -> None:
    key = "tvly-FAKE0123"

    with pytest.raises(PresetValidationError) as caught:
        WebRouter("auto", preset=key)

    assert key not in str(caught.value)


@pytest.mark.parametrize("given", ["class", "another methods backend"])
def test_validate_takes_a_provider_only_as_the_methods_own_kind_of_backend(given: str, fake_provider: Any) -> None:
    provider = FakeProvider if given == "class" else FakeProvider()
    component = "search" if given == "class" else "answer"

    check = PresetRegistry(default_provider="fake").validate(component, {"query": "q", "provider": provider})

    assert not check.ok
    assert [issue.field for issue in check.issues] == ["provider"]


def test_validate_reads_a_backend_instance_off_its_own_class(narrow_provider: Any) -> None:
    check = PresetRegistry().validate("search", {"query": "q", "country": "US", "provider": NarrowProvider()})

    assert check.provider == "narrow"
    assert [issue.field for issue in check.issues] == ["country"]


def test_a_setting_taken_in_either_case_is_the_same_in_either_case(fake_browser_provider: Any) -> None:
    client = definition("acme.client", components={"browser": {"defaults": {"proxy_country": "US"}}})
    call = definition("acme.call", components={"browser": {"defaults": {"proxy_country": "us"}}})
    router = WebRouter("fake", preset=Preset.model_validate(client))
    router.presets.add(call)

    with router.open_browser(proxy_country="us", preset="acme.call@1.0.0") as handle:
        assert handle.meta is not None and handle.meta.preset is not None
        assert handle.meta.preset.overridden == []


def test_validate_reports_a_bad_format_once_and_never_repeats_it(fake_extract_provider: Any) -> None:
    secret = "sk-FAKE-KEY-123"

    check = PresetRegistry(default_provider="fake").validate("extract", {"urls": ["u"], "format": secret})

    assert [issue.field for issue in check.issues] == ["format"]
    assert all(secret not in issue.message for issue in check.issues)


def test_validate_never_repeats_a_provider_name_no_backend_has() -> None:
    secret = "sk-FAKE-KEY-123"

    check = PresetRegistry().validate("search", {"query": "q", "provider": secret})

    assert [issue.field for issue in check.issues] == ["provider"]
    assert secret.lower() not in check.issues[0].message.lower()  # the registry lowercases a name before naming it


def test_a_read_only_mapping_is_a_definition_like_any_other() -> None:
    from types import MappingProxyType

    nested = definition()
    nested["components"] = MappingProxyType(nested["components"])

    added = PresetRegistry().add(MappingProxyType(nested))

    assert added.section("search").defaults == {"max_results": 5}
