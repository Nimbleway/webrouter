"""``contracts/`` must describe the code as it is, and say where each mapping comes from.

The generated part of every committed contract is rebuilt here from the providers, so a
mapping changed or a provider removed without ``make contracts`` fails CI. A contract is
committed once its hand-written fields are filled: all three, complete, no ``TODO``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.contracts import CONTRACTS_DIR, HAND_FIELDS, expected, providers, stale
from web_router.exceptions import ProviderNotInstalledError

VENDOR_OPTION_STATUSES = frozenset({"passthrough", "not_supported"})
VERIFIED_KINDS = ("live: ", "docs: ")


def _contracts() -> dict[str, dict[str, Any]]:
    return {
        f"{path.parent.name}/{path.stem}": json.loads(path.read_text())
        for path in sorted(CONTRACTS_DIR.glob("*/*.json"))
    }


CONTRACTS = _contracts()
FILLED = sorted(CONTRACTS)


def test_contracts_match_the_code() -> None:
    try:
        files = expected()
    except ProviderNotInstalledError as exc:
        pytest.skip(f"generating every contract needs every extra (uv sync --all-extras): {exc}")
    assert stale(files) == [], "Run `make contracts` and commit the result."


def test_every_built_in_provider_has_a_contract() -> None:
    """A provider merges with its vendor references: start one with make contracts PROVIDER=<method>/<provider>."""
    try:
        built_in = {f"{method}/{name}" for method, name in providers()}
    except ProviderNotInstalledError as exc:
        pytest.skip(f"listing every provider needs every extra (uv sync --all-extras): {exc}")
    missing = sorted(built_in - set(CONTRACTS))
    assert not missing, f"no contract for {', '.join(missing)}: run make contracts PROVIDER=<method>/<provider>"


TODO = "TODO"


def _filled(text: object) -> bool:
    return isinstance(text, str) and bool(text.strip()) and TODO not in text


@pytest.mark.parametrize("key", FILLED)
def test_a_filled_contract_is_complete(key: str) -> None:
    contract = CONTRACTS[key]
    assert all(field in contract for field in HAND_FIELDS), f"{key}: fill in all of {', '.join(HAND_FIELDS)}"
    sources = contract["source"] if isinstance(contract["source"], list) else [contract["source"]]
    assert sources and all(map(_filled, sources)), f"{key}: name the vendor's documentation in `source`"
    notes_as_urls = [s for s in sources if s.startswith("https://") and len(s.split()) > 1]
    assert not notes_as_urls, (
        f"{key}: a `source` that starts with a URL must be only the URL, or the docs watch fetches the note "
        f"too; write a note as prose that names the URL inside it: {notes_as_urls}"
    )
    assert isinstance(contract["vendor_options"], dict), (
        f"{key}: list the vendor's unmapped options in `vendor_options`"
    )
    hand_written = json.dumps({field: contract[field] for field in HAND_FIELDS})
    assert TODO not in hand_written, f"{key}: a TODO is left in {', '.join(HAND_FIELDS)}"
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
