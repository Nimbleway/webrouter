"""One vendor's Web Catalog page: an endpoint index, Input always, Output only when recorded, all safe as MDX."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from scripts.data_api_docs import DOCS, job_peers
from scripts.data_api_io import output_fields
from scripts.data_api_mdx import anchor, mdx, text
from scripts.data_api_page import DEFAULT_ICON, ICON_DIR, PAGES_DIR, icon_url, vendor_page
from web_router.vendors.catalog import builtin_catalog
from web_router.vendors.schema import Vendor


def _vendor(**endpoint: Any) -> Vendor:
    call = {
        "id": "acme.things.list",
        "name": "List things",
        "summary": "Every thing.",
        "kind": "read",
        "method": "GET",
        "path": "/things/{kind}",
        "params": {"kind": {"in": "path", "type": "string", "required": True, "example": "big"}},
        "docs_url": "https://docs.example.com/things",
        "verified": {"docs": "https://docs.example.com/things"},
        **endpoint,
    }
    return Vendor.model_validate(
        {
            "vendor": "acme",
            "name": "Acme",
            "category": "commerce",
            "summary": "Things.",
            "docs_url": "https://docs.example.com",
            "base_url": "https://api.example.com",
            "sources": ["https://docs.example.com"],
            "curated": "2026-11-08",
            "endpoints": [call],
        }
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("GET /v1/<name> with {id}", "GET /v1/&lt;name&gt; with &#123;id&#125;"),
        ("costs $0.10 per $1", "costs &#36;0.10 per &#36;1"),
        ('keeps `{"a": "$b"}` as code', 'keeps `{"a": "$b"}` as code'),
        ("an odd ` backtick <x>", "an odd ` backtick &lt;x&gt;"),
        ("C:\\data\\<file>", "C:&#92;data&#92;&lt;file&gt;"),
    ],
)
def test_mdx_escapes_markup_outside_code(value: str, expected: str) -> None:
    assert mdx(value) == expected


def test_text_makes_every_run_a_jsx_string_literal() -> None:
    assert text('send `{"a": "$b"}` & <go>') == ('{"send "}<code>{"{\\"a\\": \\"\\u0024b\\"}"}</code>{" & <go>"}')
    assert text("an odd ` backtick") == '{"an odd ` backtick"}'


def test_a_page_without_a_recorded_response_shows_input_only() -> None:
    page = vendor_page(_vendor(), {})
    assert '<div className="wc-io">' in page
    assert "Output" not in page and "wc-io-split" not in page
    assert '<code>{"kind"}</code> <span className="wc-required">{"required"}</span>' in page
    assert "router.call_data_api(\"acme.things.list\", {'kind': 'big'})" in page


def test_the_python_example_is_python_and_the_mcp_one_json() -> None:
    params = {
        "kind": {"in": "path", "type": "string", "required": True, "example": "big"},
        "all": {"in": "query", "type": "boolean", "example": True},
        "since": {"in": "query", "type": "string", "example": None},
    }
    page = vendor_page(_vendor(params=params), {})
    python = "router.call_data_api(\"acme.things.list\", {'kind': 'big', 'all': True})"
    assert python in page
    compile(python, "<example>", "eval")
    assert 'params={"kind": "big", "all": true})' in page


def test_a_recorded_response_adds_the_output_panel_and_its_example() -> None:
    page = vendor_page(_vendor(example_response={"items": [{"id": 1, "tags": ["a"]}], "next": None}), {})
    assert '<div className="wc-io wc-io-split">' in page
    assert '<div className="wc-panel-title">{"Output"}</div>' in page
    assert '<code>{"items[].id"}</code></td><td>{"number"}' in page
    assert "```json Response" in page


def test_output_fields_follow_lists_and_objects() -> None:
    fields = output_fields({"data": {"rows": [{"a": "x", "b": [1]}]}, "total": 3})
    assert fields == [
        ("data", "object"),
        ("data.rows", "object[]"),
        ("data.rows[].a", "string"),
        ("data.rows[].b", "number[]"),
        ("total", "number"),
    ]
    assert output_fields([{"id": 1}]) == [("[].id", "number")]


def test_every_endpoint_is_in_the_index_and_has_its_block() -> None:
    catalog = builtin_catalog()
    peers = job_peers(catalog)
    for vendor in catalog.all():
        page = vendor_page(vendor, peers)
        for endpoint in vendor.endpoints:
            assert f'href="#{anchor(endpoint.id)}"' in page
            assert f"{{#{anchor(endpoint.id)}}}" in page


def test_no_shipped_page_leaves_raw_text_in_its_blocks() -> None:
    """Inside the JSX blocks every run of text is a {"..."} literal, which MDX never reads as markup."""
    catalog = builtin_catalog()
    peers = job_peers(catalog)
    for vendor in catalog.all():
        page = vendor_page(vendor, peers)
        for line in page.splitlines():
            if line.startswith(("<div", "<a ", "<p ", "<tr>", "<table")):
                bare = re.sub(r'\{"(?:[^"\\]|\\.)*"\}', "", line)  # the literals
                bare = re.sub(r"<[^>]*>", "", bare)  # the elements
                assert not bare.strip(" "), (vendor.vendor, line)


def _prose_lines(page: str) -> list[str]:
    """The Markdown lines a page writes outside its JSX blocks and code fences: table rows, notes, headings."""
    lines, fenced = [], False
    for line in page.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith(("| ", "- ", "### ")):
            lines.append(re.sub(r" \{#[a-z0-9-]+\}$", "", line))  # a heading's own anchor
    return lines


def test_prose_outside_jsx_blocks_is_mdx_safe() -> None:
    """Setup rows, notes and endpoint headings are Markdown: no raw markup character outside backticks."""
    catalog = builtin_catalog()
    peers = job_peers(catalog)
    for vendor in catalog.all():
        for line in _prose_lines(vendor_page(vendor, peers)):
            outside = line.split("`")[::2] if line.count("`") % 2 == 0 else [line]
            assert not re.search(r"[<>{}$\\]", "".join(outside)), (vendor.vendor, line)


def test_catalog_text_with_markup_stays_escaped_in_prose() -> None:
    notes = ["Costs $1 per <call> {id}"]
    page = vendor_page(_vendor(name="Get <thing> {id} for $1", notes=notes), {})
    for line in _prose_lines(page):
        assert not re.search(r"[<>{}$]", line), line


def test_a_required_param_without_example_gets_no_call_example() -> None:
    params = {"kind": {"in": "path", "type": "string", "required": True}}
    page = vendor_page(_vendor(params=params), {})
    assert "router.call_data_api" not in page
    assert "call_data_api(endpoint=" not in page


def test_endpoint_anchors_are_unique_across_the_catalog() -> None:
    ids = [endpoint.id for vendor in builtin_catalog().all() for endpoint in vendor.endpoints]
    assert len({anchor(endpoint_id) for endpoint_id in ids}) == len(ids)


def test_peers_skip_account_jobs_and_never_point_to_the_same_vendor() -> None:
    catalog = builtin_catalog()
    peers = job_peers(catalog)
    assert not any(job.startswith("account.") for job in peers)
    for vendor in catalog.all():
        assert f'href="/{PAGES_DIR}/{vendor.vendor}#' not in vendor_page(vendor, peers)


def test_a_keyless_vendor_has_no_setup_section() -> None:
    page = vendor_page(_vendor(), {})
    assert "No key needed" in page and "## Setup" not in page


def test_pages_show_no_check_date_and_a_plain_endpoint_count() -> None:
    """A check date goes stale with every catalog edit, and every catalog endpoint reads, so neither is shown."""
    catalog = builtin_catalog()
    peers = job_peers(catalog)
    for vendor in catalog.all():
        page = vendor_page(vendor, peers)
        assert "Checked" not in page, vendor.vendor
        assert f'<span className="wc-stat-value">{{"{len(vendor.endpoints)}"}}</span>' in page, vendor.vendor


def test_a_vendor_without_an_icon_file_gets_the_default(tmp_path: Path) -> None:
    assert icon_url("acme", tmp_path) == DEFAULT_ICON
    page = vendor_page(_vendor(), {})
    assert f'<img className="wc-icon" src="{DEFAULT_ICON}"' in page


def test_a_vendor_icon_file_replaces_the_default(tmp_path: Path) -> None:
    (tmp_path / ICON_DIR).mkdir(parents=True)
    (tmp_path / ICON_DIR / "acme.png").write_bytes(b"png")
    assert icon_url("acme", tmp_path) == f"/{ICON_DIR}/acme.png"
    page = vendor_page(_vendor(), {}, icon_url("acme", tmp_path))
    assert f'src="/{ICON_DIR}/acme.png"' in page


def test_the_default_icon_ships_with_the_docs() -> None:
    assert (DOCS / DEFAULT_ICON.lstrip("/")).is_file()
