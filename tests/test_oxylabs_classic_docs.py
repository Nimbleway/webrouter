"""Reading Oxylabs' docs tables into the Web Scraper API catalogue: defaults come from the default column alone."""

from __future__ import annotations

import pytest

from scripts.oxylabs_classic import _entry
from scripts.oxylabs_docs import DOCS, parameters
from web_router.providers.oxylabs_classic import classic_sources

PAGE = f"{DOCS}/e-commerce/example.md"


def _table(third: str, store_id: str) -> str:
    """A docs parameter table whose third column is headed ``third``; synthetic, in the docs' HTML layout."""
    return (
        "<table><thead><tr><th>Parameter</th><th>Description</th>"
        f"<th>{third}</th></tr></thead><tbody>"
        "<tr><td><mark>source</mark></td><td>Sets <code>example_product</code></td><td>string</td></tr>"
        f"<tr><td>store_id</td><td>Store ID as an integer, e.g. 552.</td><td>{store_id}</td></tr>"
        "</tbody></table>"
    )


def _default(third: str, cell: str) -> str | None:
    found = parameters({PAGE: _table(third, cell)})
    default: str | None = found["example_product"]["params"]["store_id"]["default"]
    return default


def test_a_table_with_no_default_column_gives_no_default() -> None:
    assert _default("Type", "integer") is None
    assert _default("Example", "552") is None, "an example is not a default"


@pytest.mark.parametrize("head", ["Default", "Default value"])
def test_a_table_with_a_default_column_keeps_its_value(head: str) -> None:
    assert _default(head, "552") == "552"


def test_a_note_in_the_default_column_is_no_default() -> None:
    assert _default("Default", "marketplace default") is None


def test_a_shipped_source_with_a_sentence_for_a_default_is_refused() -> None:
    found = {"docs": PAGE.removesuffix(".md"), "params": {}}
    found["params"]["store_id"] = {"name": "store_id", "required": False, "default": "Store ID as an integer."}
    with pytest.raises(ValueError, match=r"example_product\.store_id: default .* is a sentence"):
        _entry("example_product", found)


def test_no_shipped_default_is_a_sentence() -> None:
    defaults = {
        (name, key): spec["default"]
        for name, entry in classic_sources().items()
        for key, spec in entry["inputs"].items()
        if spec["default"] is not None
    }
    assert [where for where, value in defaults.items() if len(value.split()) > 1] == []
    assert defaults[("amazon_bestsellers", "start_page")] == "1" and defaults[("amazon_product", "domain")] == "com"
    assert classic_sources()["bestbuy_product"]["inputs"]["fulfillment_type"]["default"] is None
    assert classic_sources()["amazon_product"]["inputs"]["locale"]["default"] is None
