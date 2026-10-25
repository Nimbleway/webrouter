"""``contracts/`` must describe the code as it is, and say where each mapping comes from.

The generated half of every contract is rebuilt here from the providers
themselves, so a provider added, removed or remapped without ``make contracts``
fails CI. The hand-written half -- the vendor's documentation, how each mapped
option was verified, and the vendor options web-router does not map -- must be
filled in for every contract outside :data:`PENDING`, which only shrinks.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.contracts import CONTRACTS_DIR, TODO, expected, stale
from web_router.exceptions import ProviderNotInstalledError

PENDING: frozenset[str] = frozenset(
    {
        "browse/browser-use",
        "browse/browserbase",
        "browse/skyvern",
        "browser/anchor",
        "browser/browserbase",
        "browser/hyperbrowser",
        "browser/kernel",
        "browser/steel",
        "extract/brightdata",
        "extract/deepseek",
        "extract/exa",
        "extract/firecrawl",
        "extract/jina",
        "extract/keenable",
        "extract/linkup",
        "extract/nimble",
        "extract/oxylabs",
        "extract/webiq",
        "research/linkup",
        "research/nimble",
        "research/parallel",
        "research/perplexity",
        "research/you",
        "search/brave",
        "search/deepseek",
        "search/exa",
        "search/exa-mcp",
        "search/keenable",
        "search/linkup",
        "search/nimble",
        "search/parallel",
        "search/parallel-mcp",
        "search/perplexity",
        "search/webiq",
        "search/you",
        "search/you-mcp",
    }
)
"""Contracts written before this check, whose hand-written fields are still to fill.

Remove an entry once its contract is filled in; never add one. A provider added
from here on starts outside this set, so it cannot merge with a ``TODO`` left in."""

VENDOR_OPTION_STATUSES = frozenset({"passthrough", "not_supported"})
VERIFIED_KINDS = ("live: ", "docs: ")


def _contracts() -> dict[str, dict[str, Any]]:
    return {
        f"{path.parent.name}/{path.stem}": json.loads(path.read_text())
        for path in sorted(CONTRACTS_DIR.glob("*/*.json"))
    }


CONTRACTS = _contracts()
FILLED = sorted(set(CONTRACTS) - PENDING)


def test_contracts_match_the_code() -> None:
    try:
        files = expected()
    except ProviderNotInstalledError as exc:
        pytest.skip(f"generating every contract needs every extra (uv sync --all-extras): {exc}")
    assert stale(files) == [], "Run `make contracts` and commit the result."


def test_pending_only_names_contracts_that_exist() -> None:
    assert set() == PENDING - set(CONTRACTS)


@pytest.mark.parametrize("key", sorted(PENDING & set(CONTRACTS)))
def test_a_pending_contract_leaves_pending_once_filled(key: str) -> None:
    """Keeps PENDING honest: a contract with nothing left to fill comes off the list."""
    assert TODO in json.dumps({field: CONTRACTS[key][field] for field in ("source", "verified", "vendor_options")}), (
        f"{key} is filled in; remove it from PENDING"
    )


def _filled(text: object) -> bool:
    return isinstance(text, str) and bool(text.strip()) and TODO not in text


@pytest.mark.parametrize("key", FILLED)
def test_a_filled_contract_has_no_todo_left(key: str) -> None:
    contract = CONTRACTS[key]
    sources = contract["source"] if isinstance(contract["source"], list) else [contract["source"]]
    assert sources and all(map(_filled, sources)), f"{key}: name the vendor's documentation in `source`"
    assert contract["vendor_options"] != TODO, f"{key}: list the vendor's unmapped options in `vendor_options`"
    for option, how in contract["verified"].items():
        kind = next((k for k in VERIFIED_KINDS if how.startswith(k)), None)
        assert kind and _filled(how.removeprefix(kind)), (
            f"{key}: verified[{option!r}] must be 'live: <the test>' or 'docs: <the page>'"
        )


@pytest.mark.parametrize("key", FILLED)
def test_vendor_options_never_restate_a_mapped_field(key: str) -> None:
    """An option listed as unmapped that a mapping now sends means the contract is behind the code."""
    contract = CONTRACTS[key]
    sent = {_top_field(field) for field in contract["always_sent"]}
    for values in contract["options"].values():
        for change in values.values():
            sent |= {_top_field(field) for field in change if field not in {"refused", "unchanged"}}

    for name, entry in contract["vendor_options"].items():
        assert entry["status"] in VENDOR_OPTION_STATUSES, f"{key}: {name} has status {entry['status']!r}"
        assert entry.get("note"), f"{key}: {name} needs a note"
        assert name not in sent, f"{key}: {name} is sent by a mapped option; take it out of vendor_options"


MCP_ARGUMENTS = "params.arguments."


def _top_field(field: str) -> str:
    """``body.browserSettings.verified`` -> ``browserSettings``: the name a vendor's reference lists.

    An MCP tool's options sit inside the JSON-RPC envelope, under ``params.arguments``.
    """
    rest = field.split(".", 1)[-1]
    rest = rest.removeprefix(MCP_ARGUMENTS)
    return rest.split(".", 1)[0]
