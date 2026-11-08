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


def test_validate_refuses_a_budget_where_the_method_takes_none() -> None:
    check = WebRouter().presets.validate("browser", {"budget": Budget(max_cost_usd=0.05)})

    assert [issue.field for issue in check.issues] == ["budget"]

