"""What a live data listing's rows show of each source, and what they leave out when the page cannot fit."""

from __future__ import annotations

import pytest

from web_router.mcp.shaping import render
from web_router.mcp.shaping_live_data import shape_live_data_sources
from web_router.types_live_data import LiveDataSample, LiveDataSource, LiveDataSourcePage

_PROSE = "alpha beta gamma delta " * 200


def test_a_listing_row_says_what_a_run_must_pass_without_the_schemas() -> None:
    schema = {"type": "object", "required": ["asin", "zip_code"], "properties": {"asin": {}, "zip_code": {}}}
    sample = LiveDataSample(input={"asin": "B000000000", "zip_code": "10001"}, output={"title": "Widget"})
    page = LiveDataSourcePage(
        provider="fake",
        sources=[
            LiveDataSource(provider="fake", name="keyed", input_schema=schema, samples=[sample], published=True),
            LiveDataSource(provider="fake", name="optional", input_schema={"type": "object"}, published=True),
            LiveDataSource(provider="fake", name="draft"),
            LiveDataSource(provider="fake", name="empty", input_schema={}, published=True),
        ],
    )

    shaped = shape_live_data_sources(page, "templates-0001", budget=20_000)

    keyed, optional, draft, empty = shaped["sources"]
    assert keyed["required_inputs"] == ["asin", "zip_code"]
    assert keyed["sample_inputs"] == {"asin": "B000000000", "zip_code": "10001"}
    assert "input_schema" not in keyed and "samples" not in keyed, "the schemas and the records are `template`'s"
    assert optional["required_inputs"] == [] and optional["sample_inputs"] is None
    assert draft["required_inputs"] is None, "no published schema, so nothing to read the inputs from"
    assert empty["required_inputs"] == [], "an empty schema is a published one that requires nothing"


def test_a_listing_row_shows_the_first_sample_that_names_an_input() -> None:
    samples = [LiveDataSample(output={"title": "Widget"}), LiveDataSample(input={"url": "https://example.com/p/1"})]
    fixed_page = [LiveDataSample(output={"title": "Widget"})]
    page = LiveDataSourcePage(
        provider="fake",
        sources=[
            LiveDataSource(provider="fake", name="keyed", input_schema={"required": ["url"]}, samples=samples),
            LiveDataSource(provider="fake", name="fixed", input_schema={}, samples=fixed_page),
        ],
    )

    keyed, fixed = shape_live_data_sources(page, "templates-0001", budget=20_000)["sources"]

    assert keyed["sample_inputs"] == {"url": "https://example.com/p/1"}, "not the input-less sample before it"
    assert fixed["sample_inputs"] == {}, "a fixed page's sample takes no inputs, and says so"


@pytest.mark.parametrize("rows", [1, 5])
def test_a_sample_input_too_large_for_the_listing_is_left_out_whole(rows: int) -> None:
    huge = LiveDataSample(input={"url": "https://example.com/" + "x" * 24_000})
    small = LiveDataSample(input={"url": "https://example.com/p/1"})
    schema = {"type": "object", "required": ["url"]}
    templates = [LiveDataSource(provider="fake", name="huge", input_schema=schema, samples=[huge])]
    templates += [LiveDataSource(provider="fake", name=f"t{i}", samples=[small]) for i in range(rows - 1)]

    shaped = shape_live_data_sources(
        LiveDataSourcePage(provider="fake", sources=templates), "templates-0001", budget=8_000
    )

    assert len(render(shaped)) <= 8_000
    assert shaped["source_count"] == rows, "one oversized sample does not crowd out the other rows"
    first, *rest = shaped["sources"]
    assert first["sample_inputs"] is None and first["required_inputs"] == ["url"]
    assert all(row["sample_inputs"] == small.input for row in rest)
    assert shaped["truncated"] is True
    assert "sample_inputs" in shaped["note"] and "templates-0001" in shaped["note"]


@pytest.mark.parametrize("rows", [50, 500])
def test_many_oversized_sample_inputs_share_one_note_so_the_rows_still_fit(rows: int) -> None:
    sample = LiveDataSample(input={"url": "https://x/" + "a" * 250})
    templates = [LiveDataSource(provider="fake", name=f"t{i}", samples=[sample]) for i in range(rows)]

    shaped = shape_live_data_sources(
        LiveDataSourcePage(provider="fake", sources=templates), "templates-0001", budget=8_000
    )

    assert len(render(shaped)) <= 8_000
    assert shaped["source_count"] > 1, "the notes do not crowd out the rows"
    assert all(row["sample_inputs"] is None for row in shaped["sources"])
    assert shaped["note"].count("sample_inputs") == 1 and "templates-0001" in shaped["note"]
    assert len(shaped["note"]) < 600, "one note for the page, whatever its row count"


def test_a_listing_with_small_sample_inputs_is_not_truncated() -> None:
    sample = LiveDataSample(input={"url": "https://example.com/p/1"})
    page = LiveDataSourcePage(provider="fake", sources=[LiveDataSource(provider="fake", name="t", samples=[sample])])

    shaped = shape_live_data_sources(page, "templates-0001", budget=8_000)

    assert shaped["sources"][0]["sample_inputs"] == sample.input
    assert shaped["truncated"] is False and "note" not in shaped


def test_a_listing_counts_the_rows_it_shows_not_the_rows_it_was_given() -> None:
    page = LiveDataSourcePage(
        provider="fake",
        sources=[LiveDataSource(provider="fake", name=f"t{i}", description=_PROSE) for i in range(200)],
        total=200,
    )

    shaped = shape_live_data_sources(page, "templates-0001", budget=20_000)

    assert shaped["source_count"] == len(shaped["sources"]) < 200
    assert shaped["total"] == 200, "the backend's own count of the catalogue is untouched"
