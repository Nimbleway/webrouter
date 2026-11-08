"""Presets: the precedence between a call, its preset and the router's, and the locks.

Every router method takes ``preset=``, and these run each one over the in-memory
fakes in conftest, so nothing here touches the network or needs a key. A fake's
``requests`` list is the evidence that a call did, or did not, reach the backend.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import BaseModel

from tests.conftest import FakeProvider
from tests.presets_helpers import LOCKED, REF, NarrowProvider, definition, searched
from web_router import (
    Preset,
    PresetCompatibilityError,
    PresetNotFoundError,
    PresetRegistry,
    PresetValidationError,
    WebRouter,
)


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


def test_leaving_overridable_out_locks_every_default(fake_provider: Any) -> None:
    """Only the defaults a section declares editable may change."""
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


def test_a_request_never_shares_a_nested_value_with_the_registry(fake_provider: Any) -> None:
    router = WebRouter("fake")
    nested = {"search": {"defaults": {"provider_options": {"filters": {"lang": "en"}}}}}
    router.presets.add(definition(components=nested))

    router.search("q", preset=REF)
    searched(router).requests[0].provider_options["filters"]["lang"] = "fr"
    router.search("q", preset=REF)

    assert searched(router).requests[1].provider_options == {"filters": {"lang": "en"}}
    assert router.presets.get(REF).section("search").defaults["provider_options"] == {"filters": {"lang": "en"}}


def test_a_one_shot_iterable_from_the_caller_reaches_the_request_whole(fake_provider: Any) -> None:
    router = WebRouter("fake")
    domains = {"search": {"defaults": {"include_domains": ["a.com"]}, "overridable": ["include_domains"]}}
    router.presets.add(definition(components=domains))

    router.search("q", include_domains=iter(["b.com", "c.com"]), preset=REF)  # type: ignore[arg-type]

    assert searched(router).requests[0].include_domains == ["b.com", "c.com"]


def test_a_setting_taken_in_either_case_is_the_same_in_either_case(fake_browser_provider: Any) -> None:
    client = definition("acme.client", components={"browser": {"defaults": {"proxy_country": "US"}}})
    call = definition("acme.call", components={"browser": {"defaults": {"proxy_country": "us"}}})
    router = WebRouter("fake", preset=Preset.model_validate(client))
    router.presets.add(call)

    with router.open_browser(proxy_country="us", preset="acme.call@1.0.0") as handle:
        assert handle.meta is not None and handle.meta.preset is not None
        assert handle.meta.preset.overridden == []


def test_a_lock_error_names_neither_side_of_the_conflict(fake_browse_provider: Any) -> None:
    held = "https://example.com/start?token=PRESET-ONLY"
    router = WebRouter("fake")
    router.presets.add(definition(components={"browse": {"defaults": {"start_url": held}}}))

    with pytest.raises(PresetValidationError) as caught:
        router.browse("t", start_url="https://example.com/other?token=CALL-ONLY", preset=REF)

    assert "PRESET-ONLY" not in str(caught.value) and "CALL-ONLY" not in str(caught.value)
    assert "start_url" in str(caught.value)


def test_a_backend_instance_is_the_same_setting_as_its_name(fake_provider: Any) -> None:
    router = WebRouter("auto")
    router.presets.add(definition(components={"search": {"defaults": {"provider": "fake"}}}))

    response = router.search("q", provider=FakeProvider(), preset=REF)
    assert response.meta is not None and response.meta.preset is not None
    assert response.meta.preset.overridden == []

    with pytest.raises(PresetValidationError):
        router.search("q", provider=NarrowProvider(), preset=REF)
