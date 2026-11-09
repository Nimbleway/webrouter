"""Presets: ``validate()`` takes every argument the method does, and reads each as the call would send it.

Only backends' classes are read, so nothing here touches the network or needs a key.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from web_router import AsyncWebRouter, Budget, WebRouter
from web_router.constants_presets import (
    PRESET_ARGUMENT,
    PRESET_CALL_ARGUMENTS,
    PRESET_FIELDS,
    PRESET_NATIVE_INPUTS,
    PRESET_PROVIDER_FIELD,
)
from web_router.types_meta import PresetComponent

COMPONENT_OF: dict[str, PresetComponent] = {
    "search": "search",
    "extract": "extract",
    "run_template": "templates",
    "answer": "answer",
    "research": "research",
    "submit_research": "research",
    "browse": "browse",
    "submit_browse": "browse",
    "open_browser": "browser",
}
"""Each method that takes ``preset=``, and the section of a preset it reads."""

CALLS: dict[PresetComponent, dict[str, Any]] = {
    "search": {"query": "q"},
    "extract": {"urls": "https://example.com"},
    "answer": {"query": "q"},
    "research": {"query": "q"},
    "browse": {"task": "t"},
}


@pytest.mark.parametrize("router", [WebRouter, AsyncWebRouter], ids=["sync", "async"])
def test_every_argument_a_method_takes_is_one_validate_knows(router: type) -> None:
    """A new argument on a method is a preset field, a native input or a call argument, or validate() refuses it."""
    methods = {
        name: inspect.signature(member).parameters
        for name, member in inspect.getmembers(router, inspect.isfunction)
        if not name.startswith("_") and PRESET_ARGUMENT in inspect.signature(member).parameters
    }
    assert set(methods) == set(COMPONENT_OF), "map the method to its preset section in COMPONENT_OF"
    for component, arguments in PRESET_CALL_ARGUMENTS.items():
        taken = set().union(*(methods[name] for name, mapped in COMPONENT_OF.items() if mapped == component))
        assert arguments <= taken, f"no {component} method takes {sorted(arguments - taken)}"
    for name, parameters in methods.items():
        component = COMPONENT_OF[name]
        known = (
            PRESET_FIELDS[component]
            | PRESET_NATIVE_INPUTS[component]
            | PRESET_CALL_ARGUMENTS[component]
            | {PRESET_PROVIDER_FIELD, PRESET_ARGUMENT, "self"}
        )
        assert set(parameters) <= known, f"{router.__name__}.{name} takes {sorted(set(parameters) - known)}"


@pytest.mark.parametrize("component", sorted(CALLS))
def test_validate_takes_a_budget_where_the_method_does(component: PresetComponent) -> None:
    check = WebRouter().presets.validate(component, {**CALLS[component], "budget": Budget(max_cost_usd=0.05)})

    assert check.ok, check.issues


@pytest.mark.parametrize(
    ("component", "call"),
    [
        ("search", {"query": "q", "budget": {"max_cost_usd": 1}}),
        ("research", {"query": "q", "timeout": "soon"}),
    ],
    ids=["budget-as-dict", "timeout-as-text"],
)
def test_validate_checks_a_call_argument_as_the_call_uses_it(component: PresetComponent, call: dict[str, Any]) -> None:
    """The call reads a budget's fields and waits a number of seconds: anything else fails once it runs."""
    check = WebRouter().presets.validate(component, call)

    assert [issue.field for issue in check.issues] == sorted(set(call) - set(CALLS[component]))


@pytest.mark.parametrize(
    ("component", "call"),
    [
        ("search", {"query": None}),
        ("extract", {"urls": None}),
        ("browse", {"task": None}),
        ("templates", {"template": None, "provider": "nimble"}),
    ],
    ids=["query", "urls", "task", "template"],
)
def test_a_required_input_given_as_none_is_an_issue(component: PresetComponent, call: dict[str, Any]) -> None:
    check = WebRouter().presets.validate(component, call)

    assert [issue.field for issue in check.issues] == [next(iter(call))]


def test_an_input_left_out_is_not_checked_and_an_optional_one_may_be_none() -> None:
    assert WebRouter().presets.validate("search").ok
    assert WebRouter().presets.validate("research", {"query": "q", "records": None}).ok


def test_validate_refuses_a_budget_where_the_method_takes_none() -> None:
    check = WebRouter().presets.validate("browser", {"budget": Budget(max_cost_usd=0.05)})

    assert [issue.field for issue in check.issues] == ["budget"]


def test_validate_reads_a_value_as_the_call_would_send_it() -> None:
    """``"false"`` is sent as ``False``, and Steel refuses ``persist_profile`` without a profile whatever its value."""
    check = WebRouter().presets.validate("browser", {"provider": "steel", "persist_profile": "false"})

    assert [issue.field for issue in check.issues] == ["persist_profile"]


def test_a_format_that_is_not_a_string_is_an_issue_not_a_crash() -> None:
    check = WebRouter().presets.validate("extract", {"urls": "https://example.com", "format": []})

    assert [issue.field for issue in check.issues] == ["format"]
