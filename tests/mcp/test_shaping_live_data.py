"""What the template tools' results keep whole, and what they leave out when a result cannot fit."""

from __future__ import annotations

from web_router.constants import MCP_TRUNCATION_MARKER
from web_router.mcp.shaping import render
from web_router.mcp.shaping_live_data import shape_live_data, shape_live_data_source
from web_router.types import ExtractedDocument
from web_router.types_live_data import LiveDataResponse, LiveDataSample, LiveDataSource

_BIG_SCHEMA = {"type": "object", "properties": {f"field_{index}": {"type": "string"} for index in range(400)}}


def test_a_schema_that_fits_is_never_shortened() -> None:
    template = LiveDataSource(provider="fake", name="t", input_schema=_BIG_SCHEMA, output_schema=_BIG_SCHEMA)

    shaped = shape_live_data_source(template, "template-0001", budget=100_000)

    assert shaped["source"]["input_schema"] == _BIG_SCHEMA
    assert shaped["source"]["output_schema"] == _BIG_SCHEMA
    assert shaped["truncated"] is False


def test_a_schema_that_cannot_fit_is_left_out_whole_with_a_pointer_to_the_handle() -> None:
    """A cut schema would send the model guessing at the inputs it dropped, so it goes whole or not at all."""
    template = LiveDataSource(provider="fake", name="t", input_schema={"type": "object"}, output_schema=_BIG_SCHEMA)

    shaped = shape_live_data_source(template, "template-0001", budget=2_000)

    assert len(render(shaped)) <= 2_000
    assert shaped["source"]["output_schema"] is None, "the oversized one goes"
    assert shaped["source"]["input_schema"] == {"type": "object"}, "the one that fits stays"
    assert shaped["truncated"] is True
    assert "output_schema" in shaped["note"] and "template-0001" in shaped["note"]


def test_a_record_that_cannot_fit_is_left_out_whole_with_a_pointer_to_the_handle() -> None:
    response = LiveDataResponse(
        provider="fake",
        source="t",
        data={f"field_{index}": _PROSE for index in range(10)},
        document=ExtractedDocument(url="https://example.com", content=_PROSE, format="markdown"),
    )

    shaped = shape_live_data(response, "live_data-0001", budget=3_000)

    assert len(render(shaped)) <= 3_000
    assert shaped["records"] is None
    assert MCP_TRUNCATION_MARKER in shaped["document"]["content"], "the page was cut first, as text is"
    assert shaped["truncated"] is True
    assert "data" in shaped["note"] and "live_data-0001" in shaped["note"]


def test_a_record_that_fits_once_the_page_is_cut_is_kept() -> None:
    response = LiveDataResponse(
        provider="fake",
        source="t",
        data={"title": "Widget", "price": "9.99"},
        document=ExtractedDocument(url="https://example.com", content=_PROSE, format="markdown"),
    )

    shaped = shape_live_data(response, "live_data-0001", budget=1_500)

    assert shaped["records"] == [{"title": "Widget", "price": "9.99"}]
    assert len(render(shaped)) <= 1_500


def test_a_described_template_carries_its_samples_whole() -> None:
    sample = LiveDataSample(input={"url": "https://example.com/p/1"}, output={"title": "Widget"})
    template = LiveDataSource(provider="fake", name="t", input_schema={"type": "object"}, samples=[sample])

    shaped = shape_live_data_source(template, "template-0001", budget=100_000)

    assert shaped["source"]["samples"] == [sample.model_dump()]
    assert shaped["truncated"] is False


def test_samples_that_cannot_fit_go_whole_before_the_schemas_do() -> None:
    """A recorded input with half its fields is not one a run can copy, so samples go whole or not at all."""
    big_output = {f"field_{index}": _PROSE for index in range(10)}
    template = LiveDataSource(
        provider="fake",
        name="t",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        samples=[LiveDataSample(input={"url": "https://example.com"}, output=big_output)],
    )

    shaped = shape_live_data_source(template, "template-0001", budget=2_000)

    assert len(render(shaped)) <= 2_000
    assert shaped["source"]["samples"] is None, "the oversized samples go"
    assert shaped["source"]["input_schema"] == {"type": "object"}, "the schemas that fit stay"
    assert shaped["source"]["output_schema"] == {"type": "object"}
    assert shaped["truncated"] is True
    assert "samples" in shaped["note"] and "template-0001" in shaped["note"]


def test_a_run_does_not_echo_its_inputs_so_they_cannot_crowd_out_the_record() -> None:
    response = LiveDataResponse(
        provider="fake",
        source="t",
        inputs={f"p{i}": _PROSE for i in range(100)},
        data={"title": "Widget"},
    )

    shaped = shape_live_data(response, "live_data-0001", budget=20_000)

    assert "inputs" not in shaped
    assert shaped["records"] == [{"title": "Widget"}]
    assert len(render(shaped)) <= 20_000


def test_the_larger_schema_is_dropped_first_so_the_smaller_one_stays() -> None:
    big = {"type": "object", "properties": {f"field_{i}": {"type": "string"} for i in range(1000)}}
    small = {"type": "object", "properties": {f"field_{i}": {"type": "string"} for i in range(60)}}
    template = LiveDataSource(provider="fake", name="t", input_schema=big, output_schema=small)

    shaped = shape_live_data_source(template, "template-0001", budget=20_000)

    assert shaped["source"]["input_schema"] is None, "the 30k schema goes"
    assert shaped["source"]["output_schema"] == small, "the 3k schema stays"
    assert len(render(shaped)) <= 20_000


_PROSE = "alpha beta gamma delta " * 200
