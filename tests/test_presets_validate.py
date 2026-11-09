"""Presets: ``validate()``, a call checked against its presets and its backend's class without being made.

Every router method takes ``preset=``, and these run each one over the in-memory
fakes in conftest, so nothing here touches the network or needs a key. A fake's
``requests`` list is the evidence that a call did, or did not, reach the backend.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import FakeProvider
from tests.presets_helpers import REF, NarrowProvider, definition
from web_router import (
    PresetRegistry,
    WebRouter,
)


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


def test_validate_takes_a_jobs_timeout_as_the_method_does(fake_research_provider: Any) -> None:
    router = WebRouter("fake")
    router.presets.add(definition(components={"research": {"defaults": {"effort": "low"}}}))

    check = router.presets.validate("research", {"query": "q", "timeout": 60}, preset="acme.research@1.0.0")

    assert check.ok


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


def test_validate_runs_the_backends_own_class_checks(fake_browse_provider: Any, fake_browser_provider: Any) -> None:
    from tests.conftest import FakeBrowseProvider, FakeBrowserProvider
    from web_router.registry import (
        register_browse_provider,
        register_browser_provider,
        unregister_browse_provider,
        unregister_browser_provider,
    )

    class Needy(FakeBrowseProvider):
        name = "needy"
        required_parameters = frozenset({"start_url"})

    class OneLevel(FakeBrowserProvider):
        name = "onelevel"
        stealth_levels = frozenset({"low"})

    register_browse_provider("needy", Needy)
    register_browser_provider("onelevel", OneLevel)
    try:
        registry = PresetRegistry(default_provider="auto")
        needs = registry.validate("browse", {"task": "t", "provider": "needy"})
        refuses_value = registry.validate("browser", {"provider": "onelevel", "stealth": "high"})
        refuses_option = registry.validate("browser", {"provider_options": {"x": 1}})
    finally:
        unregister_browse_provider("needy")
        unregister_browser_provider("onelevel")

    assert [issue.field for issue in needs.issues] == ["start_url"]
    assert [issue.field for issue in refuses_value.issues] == ["stealth"]
    assert "high" not in refuses_value.issues[0].message
    assert [issue.field for issue in refuses_option.issues] == ["provider_options"]


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({"records": [{"name": "Acme"}]}, "output_schema"),
        ({"records": [{"name": "Acme"}], "output_schema": {"type": "object"}, "effort": "low"}, "effort"),
    ],
    ids=["records need a schema", "records refuse an effort"],
)
def test_validate_reads_the_native_inputs_a_backends_own_checks_need(arguments: dict[str, Any], field: str) -> None:
    pytest.importorskip("nimble_python")

    check = PresetRegistry(default_provider="nimble").validate("research", {"query": "q", **arguments})

    assert [issue.field for issue in check.issues] == [field]


class Ambiguous(list):  # type: ignore[type-arg]
    """A list whose truth value raises, as a multi-element NumPy array's does."""

    def __bool__(self) -> bool:
        raise ValueError("the truth value is ambiguous")


@pytest.mark.parametrize(
    ("records", "fields"),
    [(Ambiguous([{"name": "Acme"}, {"name": "Beta"}]), ["output_schema"]), ("not a list", ["records"])],
    ids=["records that cannot be tested for truth", "records of the wrong type"],
)
def test_validate_reports_malformed_native_inputs_instead_of_crashing(records: Any, fields: list[str]) -> None:
    pytest.importorskip("nimble_python")

    check = PresetRegistry(default_provider="nimble").validate("research", {"query": "q", "records": records})

    assert [issue.field for issue in check.issues] == fields


def test_validate_takes_native_inputs_shaped_as_the_method_takes_them(fake_extract_provider: Any) -> None:
    check = PresetRegistry(default_provider="fake").validate("extract", {"urls": "https://example.com"})

    assert check.ok
