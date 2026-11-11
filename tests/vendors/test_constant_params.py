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
