"""The router wire format's bounds on a decision: the confidence range and the entry limit, each held by the client."""

from __future__ import annotations

import pytest

from web_router.constants_remote import WIRE_MAX_NAME, WIRE_MAX_NAMES
from web_router.routing.wire import WireError, decision_from_wire
from web_router.routing.wire_schema import response_schema


@pytest.mark.parametrize("confidence", [2, -0.1])
def test_a_decision_whose_confidence_is_outside_zero_to_one_is_refused(confidence: float) -> None:
    with pytest.raises(WireError, match="does not fit"):
        decision_from_wire({"schema": 1, "confidence": confidence})


@pytest.mark.parametrize("confidence", [0, 1])
def test_a_decision_whose_confidence_is_zero_or_one_is_kept(confidence: float) -> None:
    assert decision_from_wire({"schema": 1, "confidence": confidence}).confidence == confidence


def test_a_list_inside_a_methods_options_is_held_to_the_entry_limit() -> None:
    urls = [f"https://e{i}.example.com/" for i in range(500)]
    with pytest.raises(WireError, match=f"more than {WIRE_MAX_NAMES} entries"):
        decision_from_wire({"schema": 1, "params": {"extract": {"urls": urls}}})
    kept = decision_from_wire({"schema": 1, "params": {"extract": {"urls": urls[:WIRE_MAX_NAMES], "markdown": True}}})
    assert kept.params is not None and len(kept.params["extract"]["urls"]) == WIRE_MAX_NAMES


def test_the_response_schema_caps_a_list_or_mapping_inside_a_methods_options() -> None:
    option = response_schema()["properties"]["params"]["anyOf"][0]["additionalProperties"]["additionalProperties"]
    assert option == {"maxItems": WIRE_MAX_NAMES, "maxProperties": WIRE_MAX_NAMES}, "untyped: any JSON, capped"


def test_every_name_a_decision_sets_is_cut_short() -> None:
    long = "search:" + "x" * 20_000
    wire = {"schema": 1, "exclude": {long: "why"}, "providers": {"search": [long]}, "params": {"search": {long: 1}}}
    decision = decision_from_wire(wire)
    assert decision.providers is not None and decision.params is not None
    names = [*decision.exclude, *decision.providers["search"], *decision.params["search"]]
    assert len(names) == 3 and all(name == long[:WIRE_MAX_NAME] + "\u2026" for name in names)
    exact = "search:" + "y" * (WIRE_MAX_NAME - 7)
    assert decision_from_wire({"schema": 1, "exclude": {exact + "z": "why"}}).exclude != {exact: "why"}, "no match"
