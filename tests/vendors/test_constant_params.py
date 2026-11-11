"""A parameter whose enum allows one value is a constant: any other value is refused before anything is sent."""

from __future__ import annotations

import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.vendors.catalog import builtin_catalog
from web_router.vendors.plan import plan_endpoint

_ITEM = {"items": 1, "item0": "example.com"}


def _plan(params: dict[str, object]) -> object:
    catalog = builtin_catalog()
    return plan_endpoint(catalog.vendor("majestic"), catalog.endpoint("majestic.index.item_info"), params)


def test_a_constant_set_to_another_command_is_refused() -> None:
    with pytest.raises(UnsupportedParameterError, match="cmd='GetIndexItemInfo'"):
        _plan({**_ITEM, "cmd": "AddToBucket"})


def test_the_constant_itself_is_sent() -> None:
    plan = _plan({**_ITEM, "cmd": "GetIndexItemInfo"})
    assert ("cmd", "GetIndexItemInfo") in plan.query  # type: ignore[attr-defined]


def test_a_json_true_matches_a_constant_written_as_the_string_true() -> None:
    catalog = builtin_catalog()
    endpoint = catalog.endpoint("signalbase.funding.search")
    params = {name: spec.example for name, spec in endpoint.params.items() if spec.required}
    plan = plan_endpoint(catalog.vendor("signalbase"), endpoint, {**params, "count": True})
    assert plan is not None


def _enrow(settings: dict[str, object]) -> object:
    catalog = builtin_catalog()
    params = {"fullname": "Jane Doe", "company_domain": "example.com", "settings": settings}
    return plan_endpoint(catalog.vendor("enrow"), catalog.endpoint("enrow.email.find"), params)


def test_a_webhook_inside_an_object_input_is_refused() -> None:
    with pytest.raises(UnsupportedParameterError, match=r"settings\.webhook"):
        _enrow({"webhook": "https://example.org/x"})


def test_the_object_input_still_takes_its_documented_keys() -> None:
    assert _enrow({"country_code": "US"}).body["settings"] == {"country_code": "US"}  # type: ignore[attr-defined]


def test_a_list_for_an_input_declared_one_string_goes_as_one_json_value() -> None:
    catalog = builtin_catalog()
    endpoint = catalog.endpoint("meta-ads.campaigns.list")
    required = {
        name: spec.example for name, spec in endpoint.params.items() if spec.required or spec.location == "path"
    }
    plan = plan_endpoint(catalog.vendor("meta-ads"), endpoint, {**required, "effective_status": ["ACTIVE", "PAUSED"]})
    assert [pair for pair in plan.query if pair[0] == "effective_status"] == [  # type: ignore[attr-defined]
        ("effective_status", '["ACTIVE","PAUSED"]')
    ]
