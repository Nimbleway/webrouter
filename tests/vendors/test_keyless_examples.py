"""Keyless catalog entries sent with their own examples: the requests they build, offline.

Each pinned case was a live failure: an example the API rejects, a path that only redirects (and
redirects are never followed), or a default ``Accept`` the API answers with 406.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tests.vendors.conftest import Recorder
from web_router.vendors.caller import VendorCaller
from web_router.vendors.catalog import builtin_catalog
from web_router.vendors.plan import plan_endpoint
from web_router.vendors.schema_endpoint import VendorEndpoint

SHIPPED = builtin_catalog()


def _examples(endpoint: VendorEndpoint) -> dict[str, Any]:
    return {name: param.example for name, param in endpoint.params.items() if param.example is not None}


def _keyless_with_examples() -> list[VendorEndpoint]:
    return [
        endpoint
        for vendor in SHIPPED.all()
        if not vendor.auth.credentials and not vendor.settings
        for endpoint in vendor.endpoints
        if all(param.example is not None for param in endpoint.params.values() if param.required)
    ]


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def caller(recorder: Recorder) -> Iterator[VendorCaller]:
    made = VendorCaller({}, catalog=SHIPPED, transport=httpx.MockTransport(recorder))
    yield made
    made.close()


def _send(caller: VendorCaller, endpoint_id: str, **overrides: Any) -> None:
    endpoint = SHIPPED.endpoint(endpoint_id)
    params = {**_examples(endpoint), **overrides}
    caller.send(plan_endpoint(SHIPPED.vendor(endpoint.vendor), endpoint, params))


@pytest.mark.parametrize("endpoint", _keyless_with_examples(), ids=lambda endpoint: endpoint.id)
def test_a_keyless_endpoint_sends_from_its_own_examples(endpoint: VendorEndpoint, caller: VendorCaller) -> None:
    """Every input an example fills is one the endpoint takes, so the call plans and sends."""
    _send(caller, endpoint.id)


@pytest.mark.parametrize(
    ("endpoint_id", "either", "other"),
    [("eurostat.statistics", "time", "lastTimePeriod"), ("osv.query", "version", "commit")],
)
def test_no_example_fills_both_of_two_alternative_inputs(endpoint_id: str, either: str, other: str) -> None:
    """The API refuses a call that sets both, so the examples set at most one."""
    examples = _examples(SHIPPED.endpoint(endpoint_id))
    assert not (either in examples and other in examples)


def test_ibm_global_catalog_calls_the_versioned_paths(caller: VendorCaller, recorder: Recorder) -> None:
    """The unversioned paths answer with a redirect to /api/v1."""
    _send(caller, "ibm-global-catalog.entries.search")
    assert recorder.last.url.path == "/api/v1"
    _send(caller, "ibm-global-catalog.pricing.get", id="cloudantnosqldb")
    assert recorder.last.url.path == "/api/v1/cloudantnosqldb/pricing"


def test_legislation_gov_uk_asks_for_xml_and_searches_the_listing(caller: VendorCaller, recorder: Recorder) -> None:
    """The default JSON ``Accept`` got 406, and /search only redirects to a listing."""
    _send(caller, "legislation-gov-uk.legislation.get")
    assert "application/xml" in recorder.last.headers["Accept"]
    assert "application/json" not in recorder.last.headers["Accept"]
    _send(caller, "legislation-gov-uk.legislation.search")
    assert recorder.last.url.path == "/all"
    assert recorder.last.url.params["title"] == "Transport"


def test_ccmixter_sends_a_limit(caller: VendorCaller, recorder: Recorder) -> None:
    """Without one, the site's answer has headers too large to read."""
    _send(caller, "ccmixter.uploads.search")
    assert recorder.last.url.params["limit"] == "10"


def test_unesco_uis_data_names_an_indicator_and_a_country(caller: VendorCaller, recorder: Recorder) -> None:
    """The API answers 400 without both."""
    _send(caller, "unesco-uis.indicators.data")
    assert recorder.last.url.params.get_list("indicator") == ["CR.1"]
    assert recorder.last.url.params.get_list("geoUnit") == ["BRA"]
