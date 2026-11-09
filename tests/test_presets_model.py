"""A preset holds one method's own arguments, checked as that method checks them."""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from web_router import AsyncWebRouter, Preset, WebRouter
from web_router.presets import PRESET_TOOLS, tool_params

VALID_PARAMS: dict[str, dict[str, Any]] = {
    "search": {"provider": "fake", "max_results": 5, "search_depth": "fast", "country": "US"},
    "extract": {"format": "markdown"},
    "answer": {"recency": "week", "include_domains": ["example.com"]},
    "research": {"effort": "low", "output_schema": {"type": "object"}, "timeout": 30},
    "browse": {"start_url": "https://example.com", "max_steps": 3, "timeout": 60.0},
    "run_template": {"format": "markdown", "inputs": {"url": "https://books.example"}},
    "open_browser": {"stealth": "low", "viewport": {"width": 800, "height": 600}, "headless": True},
}

WRONG_TYPES: dict[str, tuple[str, Any]] = {
    "search": ("max_results", 0),
    "extract": ("format", "pdf"),
    "answer": ("recency", "decade"),
    "research": ("effort", "huge"),
    "browse": ("max_steps", "many"),
    "run_template": ("inputs", "not a mapping"),
    "open_browser": ("stealth", "invisible"),
}


def _refused(**fields: Any) -> list[dict[str, Any]]:
    with pytest.raises(ValidationError) as refused:
        Preset(**fields)
    return refused.value.errors()


@pytest.mark.parametrize("tool", PRESET_TOOLS)
def test_each_tool_keeps_its_own_params_in_json_form(tool: str) -> None:
    preset = Preset(name="p", tool=tool, params=VALID_PARAMS[tool])  # type: ignore[arg-type]

    assert preset.params == VALID_PARAMS[tool]


@pytest.mark.parametrize("tool", PRESET_TOOLS)
def test_a_value_the_method_would_refuse_is_refused_at_its_name(tool: str) -> None:
    key, value = WRONG_TYPES[tool]

    assert [error["loc"] for error in _refused(name="p", tool=tool, params={key: value})] == [(key,)]


@pytest.mark.parametrize("tool", PRESET_TOOLS)
def test_a_param_the_tool_does_not_take_is_named_with_what_the_tool_takes(tool: str) -> None:
    errors = _refused(name="p", tool=tool, params={"budget": 1})

    takes = ", ".join(tool_params(tool))
    article = "an" if tool[0] in "aeiou" else "a"
    assert [(error["loc"], error["msg"]) for error in errors] == [
        (("budget",), f"`budget` is not {article} {tool} param; {tool} takes: {takes}")
    ]


@pytest.mark.parametrize("tool", PRESET_TOOLS)
def test_every_param_is_optional(tool: str) -> None:
    assert Preset(name="p", tool=tool).params == {}  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("tool", "key"),
    [
        ("search", "query"),
        ("extract", "urls"),
        ("browse", "task"),
        ("run_template", "template"),
        ("research", "records"),
    ],
)
def test_the_calls_own_input_is_never_a_param(tool: str, key: str) -> None:
    assert [error["loc"] for error in _refused(name="p", tool=tool, params={key: "x"})] == [(key,)]


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        ("search", {"max_results": 101}),
        ("search", {"include_domains": ["a.com"] * 51}),
        ("search", {"country": "U\x00S"}),
        ("research", {"output_schema": {}}),
        ("research", {"include_domains": []}),
        ("research", {"exclude_domains": [""]}),
        ("browse", {"start_url": "ftp://example.com"}),
        ("browse", {"max_steps": 10_001}),
        ("browse", {"allowed_domains": ["a.com"] * 51}),
        ("answer", {"provider": "nul\x00"}),
    ],
)
def test_a_preset_holds_the_managed_presets_limits(tool: str, params: dict[str, Any]) -> None:
    assert [error["loc"][0] for error in _refused(name="p", tool=tool, params=params)] == [next(iter(params))]


def test_a_schema_nested_past_32_levels_is_refused() -> None:
    schema: dict[str, Any] = {"type": "object"}
    for _ in range(33):
        schema = {"type": "object", "properties": {"x": schema}}

    assert [error["loc"] for error in _refused(name="p", tool="research", params={"output_schema": schema})] == [
        ("output_schema",)
    ]


def test_a_param_set_to_none_is_unset() -> None:
    assert Preset(name="p", tool="research", params={"timeout": None, "effort": "low"}).params == {"effort": "low"}


@pytest.mark.parametrize("name", ["", "has space", "dash-ed", "dotted.name", "ünïcode", "a" * 101])
def test_a_name_that_cannot_name_a_preset_is_refused(name: str) -> None:
    assert [error["loc"] for error in _refused(name=name, tool="search")] == [("name",)]


def test_a_tool_the_router_does_not_have_is_refused() -> None:
    assert [error["loc"] for error in _refused(name="p", tool="crawl")] == [("tool",)]


def test_a_description_a_database_cannot_keep_is_refused() -> None:
    assert [error["loc"] for error in _refused(name="p", tool="search", description="nul\x00")] == [("description",)]


def test_a_provider_given_as_the_enum_is_kept_as_its_name() -> None:
    from web_router import Provider

    assert Preset(name="p", tool="search", params={"provider": Provider.NIMBLE}).params == {"provider": "nimble"}


@pytest.mark.parametrize("tool", PRESET_TOOLS)
@pytest.mark.parametrize("router", [WebRouter, AsyncWebRouter])
def test_a_tool_takes_every_optional_argument_its_method_does_but_budget(tool: str, router: type) -> None:
    method = inspect.signature(getattr(router, tool))
    optional = {
        name
        for name, parameter in method.parameters.items()
        if name not in {"self", "budget", "preset", "records"} and parameter.default is not parameter.empty
    }

    assert set(tool_params(tool)) == optional


@pytest.mark.parametrize("params", [[], "", False, None])
def test_params_that_are_not_a_mapping_are_refused_not_read_as_none(params: Any) -> None:
    with pytest.raises(ValidationError):
        Preset.model_validate({"name": "p", "tool": "search", "params": params})


def test_a_start_url_with_a_trailing_newline_is_refused() -> None:
    assert [e["loc"][0] for e in _refused(name="p", tool="browse", params={"start_url": "https://e.com\n"})] == [
        "start_url"
    ]


def test_a_schema_json_cannot_encode_is_refused_at_its_field() -> None:
    schema = {"type": "object", "title": "\ud800"}

    assert [e["loc"] for e in _refused(name="p", tool="research", params={"output_schema": schema})] == [
        ("output_schema",)
    ]
