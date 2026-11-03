"""What the template tools' results keep whole, and what they leave out when a result cannot fit."""

from __future__ import annotations

import pytest

from web_router.constants import MCP_TRUNCATION_MARKER
from web_router.mcp.shaping import render
from web_router.mcp.shaping_templates import shape_template, shape_template_run, shape_templates
from web_router.types import ExtractedDocument, Template, TemplatePage, TemplateResponse, TemplateSample

_PROSE = "alpha beta gamma delta " * 200
_BIG_SCHEMA = {"type": "object", "properties": {f"field_{index}": {"type": "string"} for index in range(400)}}


def test_a_schema_that_fits_is_never_shortened() -> None:
    template = Template(provider="fake", name="t", input_schema=_BIG_SCHEMA, output_schema=_BIG_SCHEMA)

    shaped = shape_template(template, "template-0001", budget=100_000)

    assert shaped["template"]["input_schema"] == _BIG_SCHEMA
    assert shaped["template"]["output_schema"] == _BIG_SCHEMA
    assert shaped["truncated"] is False


def test_a_schema_that_cannot_fit_is_left_out_whole_with_a_pointer_to_the_handle() -> None:
    """A cut schema would send the model guessing at the inputs it dropped, so it goes whole or not at all."""
    template = Template(provider="fake", name="t", input_schema={"type": "object"}, output_schema=_BIG_SCHEMA)

    shaped = shape_template(template, "template-0001", budget=2_000)

    assert len(render(shaped)) <= 2_000
    assert shaped["template"]["output_schema"] is None, "the oversized one goes"
    assert shaped["template"]["input_schema"] == {"type": "object"}, "the one that fits stays"
    assert shaped["truncated"] is True
    assert "output_schema" in shaped["note"] and "template-0001" in shaped["note"]


def test_a_record_that_cannot_fit_is_left_out_whole_with_a_pointer_to_the_handle() -> None:
    response = TemplateResponse(
        provider="fake",
        template="t",
        data={f"field_{index}": _PROSE for index in range(10)},
        document=ExtractedDocument(url="https://example.com", content=_PROSE, format="markdown"),
    )

    shaped = shape_template_run(response, "run_template-0001", budget=3_000)

    assert len(render(shaped)) <= 3_000
    assert shaped["data"] is None
    assert MCP_TRUNCATION_MARKER in shaped["document"]["content"], "the page was cut first, as text is"
    assert shaped["truncated"] is True
    assert "data" in shaped["note"] and "run_template-0001" in shaped["note"]


def test_a_record_that_fits_once_the_page_is_cut_is_kept() -> None:
    response = TemplateResponse(
        provider="fake",
        template="t",
        data={"title": "Widget", "price": "9.99"},
        document=ExtractedDocument(url="https://example.com", content=_PROSE, format="markdown"),
    )

    shaped = shape_template_run(response, "run_template-0001", budget=1_500)

    assert shaped["data"] == {"title": "Widget", "price": "9.99"}
    assert len(render(shaped)) <= 1_500


def test_a_described_template_carries_its_samples_whole() -> None:
    sample = TemplateSample(input={"url": "https://example.com/p/1"}, output={"title": "Widget"})
    template = Template(provider="fake", name="t", input_schema={"type": "object"}, samples=[sample])

    shaped = shape_template(template, "template-0001", budget=100_000)

    assert shaped["template"]["samples"] == [sample.model_dump()]
    assert shaped["truncated"] is False


def test_samples_that_cannot_fit_go_whole_before_the_schemas_do() -> None:
    """A recorded input with half its fields is not one a run can copy, so samples go whole or not at all."""
    big_output = {f"field_{index}": _PROSE for index in range(10)}
    template = Template(
        provider="fake",
        name="t",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        samples=[TemplateSample(input={"url": "https://example.com"}, output=big_output)],
    )

    shaped = shape_template(template, "template-0001", budget=2_000)

    assert len(render(shaped)) <= 2_000
    assert shaped["template"]["samples"] is None, "the oversized samples go"
    assert shaped["template"]["input_schema"] == {"type": "object"}, "the schemas that fit stay"
    assert shaped["template"]["output_schema"] == {"type": "object"}
    assert shaped["truncated"] is True
    assert "samples" in shaped["note"] and "template-0001" in shaped["note"]


def test_a_listing_row_says_what_a_run_must_pass_without_the_schemas() -> None:
    schema = {"type": "object", "required": ["asin", "zip_code"], "properties": {"asin": {}, "zip_code": {}}}
    sample = TemplateSample(input={"asin": "B000000000", "zip_code": "10001"}, output={"title": "Widget"})
    page = TemplatePage(
        provider="fake",
        templates=[
            Template(provider="fake", name="keyed", input_schema=schema, samples=[sample], published=True),
            Template(provider="fake", name="optional", input_schema={"type": "object"}, published=True),
            Template(provider="fake", name="draft"),
            Template(provider="fake", name="empty", input_schema={}, published=True),
        ],
    )

    shaped = shape_templates(page, "templates-0001", budget=20_000)

    keyed, optional, draft, empty = shaped["templates"]
    assert keyed["required_inputs"] == ["asin", "zip_code"]
    assert keyed["sample_inputs"] == {"asin": "B000000000", "zip_code": "10001"}
    assert "input_schema" not in keyed and "samples" not in keyed, "the schemas and the records are `template`'s"
    assert optional["required_inputs"] == [] and optional["sample_inputs"] is None
    assert draft["required_inputs"] is None, "no published schema, so nothing to read the inputs from"
    assert empty["required_inputs"] == [], "an empty schema is a published one that requires nothing"


def test_a_listing_row_shows_the_first_sample_that_names_an_input() -> None:
    samples = [TemplateSample(output={"title": "Widget"}), TemplateSample(input={"url": "https://example.com/p/1"})]
    fixed_page = [TemplateSample(output={"title": "Widget"})]
    page = TemplatePage(
        provider="fake",
        templates=[
            Template(provider="fake", name="keyed", input_schema={"required": ["url"]}, samples=samples),
            Template(provider="fake", name="fixed", input_schema={}, samples=fixed_page),
        ],
    )

    keyed, fixed = shape_templates(page, "templates-0001", budget=20_000)["templates"]

    assert keyed["sample_inputs"] == {"url": "https://example.com/p/1"}, "not the input-less sample before it"
    assert fixed["sample_inputs"] == {}, "a fixed page's sample takes no inputs, and says so"


@pytest.mark.parametrize("rows", [1, 5])
def test_a_sample_input_too_large_for_the_listing_is_left_out_whole(rows: int) -> None:
    huge = TemplateSample(input={"url": "https://example.com/" + "x" * 24_000})
    small = TemplateSample(input={"url": "https://example.com/p/1"})
    schema = {"type": "object", "required": ["url"]}
    templates = [Template(provider="fake", name="huge", input_schema=schema, samples=[huge])]
    templates += [Template(provider="fake", name=f"t{i}", samples=[small]) for i in range(rows - 1)]

    shaped = shape_templates(TemplatePage(provider="fake", templates=templates), "templates-0001", budget=8_000)

    assert len(render(shaped)) <= 8_000
    assert shaped["template_count"] == rows, "one oversized sample does not crowd out the other rows"
    first, *rest = shaped["templates"]
    assert first["sample_inputs"] is None and first["required_inputs"] == ["url"]
    assert all(row["sample_inputs"] == small.input for row in rest)
    assert shaped["truncated"] is True
    assert "sample_inputs" in shaped["note"] and "templates-0001" in shaped["note"]


@pytest.mark.parametrize("rows", [50, 500])
def test_many_oversized_sample_inputs_share_one_note_so_the_rows_still_fit(rows: int) -> None:
    sample = TemplateSample(input={"url": "https://x/" + "a" * 250})
    templates = [Template(provider="fake", name=f"t{i}", samples=[sample]) for i in range(rows)]

    shaped = shape_templates(TemplatePage(provider="fake", templates=templates), "templates-0001", budget=8_000)

    assert len(render(shaped)) <= 8_000
    assert shaped["template_count"] > 1, "the notes do not crowd out the rows"
    assert all(row["sample_inputs"] is None for row in shaped["templates"])
    assert shaped["note"].count("sample_inputs") == 1 and "templates-0001" in shaped["note"]
    assert len(shaped["note"]) < 600, "one note for the page, whatever its row count"


def test_a_listing_with_small_sample_inputs_is_not_truncated() -> None:
    sample = TemplateSample(input={"url": "https://example.com/p/1"})
    page = TemplatePage(provider="fake", templates=[Template(provider="fake", name="t", samples=[sample])])

    shaped = shape_templates(page, "templates-0001", budget=8_000)

    assert shaped["templates"][0]["sample_inputs"] == sample.input
    assert shaped["truncated"] is False and "note" not in shaped


def test_a_listing_counts_the_rows_it_shows_not_the_rows_it_was_given() -> None:
    page = TemplatePage(
        provider="fake",
        templates=[Template(provider="fake", name=f"t{i}", description=_PROSE) for i in range(200)],
        total=200,
    )

    shaped = shape_templates(page, "templates-0001", budget=20_000)

    assert shaped["template_count"] == len(shaped["templates"]) < 200
    assert shaped["total"] == 200, "the backend's own count of the catalogue is untouched"


def test_a_run_does_not_echo_its_inputs_so_they_cannot_crowd_out_the_record() -> None:
    response = TemplateResponse(
        provider="fake",
        template="t",
        inputs={f"p{i}": _PROSE for i in range(100)},
        data={"title": "Widget"},
    )

    shaped = shape_template_run(response, "run_template-0001", budget=20_000)

    assert "inputs" not in shaped
    assert shaped["data"] == {"title": "Widget"}
    assert len(render(shaped)) <= 20_000


def test_the_larger_schema_is_dropped_first_so_the_smaller_one_stays() -> None:
    big = {"type": "object", "properties": {f"field_{i}": {"type": "string"} for i in range(1000)}}
    small = {"type": "object", "properties": {f"field_{i}": {"type": "string"} for i in range(60)}}
    template = Template(provider="fake", name="t", input_schema=big, output_schema=small)

    shaped = shape_template(template, "template-0001", budget=20_000)

    assert shaped["template"]["input_schema"] is None, "the 30k schema goes"
    assert shaped["template"]["output_schema"] == small, "the 3k schema stays"
    assert len(render(shaped)) <= 20_000
