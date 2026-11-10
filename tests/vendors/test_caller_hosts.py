"""Sending vendor calls: body credentials, a renamed client id, named hosts and the base-URL rules."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.vendors.conftest import Recorder
from tests.vendors.data import CREDENTIALS
from web_router.exceptions import ConfigurationError, MissingParameterError
from web_router.vendors.caller import VendorCaller
from web_router.vendors.catalog import VendorCatalog
from web_router.vendors.plan import plan_endpoint, plan_raw

# -- format additions: body credentials, renamed client id, named hosts -----


def _vendor(**changes: Any) -> Any:
    from tests.vendors.data import QUERYKEY
    from web_router.vendors.schema import Vendor

    return Vendor.model_validate({**QUERYKEY, **changes})


def test_a_body_credential_is_merged_into_the_json_body(recorder: Recorder) -> None:
    vendor = _vendor(
        auth={
            "credentials": {"key": {"env": ["QUERYKEY_KEY"]}, "secret": {"env": ["QUERYKEY_SECRET"]}},
            "inject": [
                {"in": "body", "name": "api_key", "value": "{key}"},
                {"in": "body", "name": "api_secret", "value": "{secret}"},
            ],
        }
    )
    caller = VendorCaller(
        {"querykey": {"key": "k-1234", "secret": "s-5678"}},
        catalog=VendorCatalog([vendor]),
        transport=httpx.MockTransport(recorder),
    )
    plan = plan_raw(vendor, "POST", "/lookup", query=None, body={"msisdn": "1", "api_key": "caller"}, headers=None)
    caller.send(plan, allow_writes=True)
    assert recorder.body() == {"msisdn": "1", "api_key": "k-1234", "api_secret": "s-5678"}

    plan = plan_raw(vendor, "POST", "/lookup", query=None, body=["not", "an", "object"], headers=None)
    with pytest.raises(ConfigurationError, match="JSON object body"):
        caller.send(plan, allow_writes=True)
    caller.close()


def test_a_renamed_client_id_field(catalog: VendorCatalog, recorder: Recorder) -> None:
    from tests.vendors.data import OAUTH
    from web_router.vendors.schema import Vendor

    oauth = {**OAUTH["auth"]["oauth2"], "client_id_param": "client_key"}
    vendor = Vendor.model_validate({**OAUTH, "auth": {**OAUTH["auth"], "oauth2": oauth}})
    caller = VendorCaller(CREDENTIALS, catalog=VendorCatalog([vendor]), transport=httpx.MockTransport(recorder))
    found = vendor.endpoints[0]
    caller.send(plan_endpoint(vendor, found, {"customer_id": "1", "query": "q"}))
    form = dict(httpx.QueryParams(recorder.token_calls[0].content.decode()))
    assert form["client_key"] == "client-123"
    assert "client_id" not in form
    caller.close()


def test_named_hosts(recorder: Recorder) -> None:
    from tests.vendors.data import _endpoint

    vendor = _vendor(
        hosts={"bulk": "https://bulk.querykey.test/v1"},
        endpoints=[
            _endpoint("querykey.domain.overview", "GET", "/overview"),
            _endpoint("querykey.bulk.status", "GET", "/status", host="bulk"),
        ],
    )
    caller = VendorCaller(CREDENTIALS, catalog=VendorCatalog([vendor]), transport=httpx.MockTransport(recorder))
    caller.send(plan_endpoint(vendor, vendor.endpoints[1], {}))
    assert str(recorder.last.url).startswith("https://bulk.querykey.test/v1/status")
    caller.send(plan_raw(vendor, "GET", "/files", query=None, body=None, headers=None, host="bulk"))
    assert recorder.last.url.host == "bulk.querykey.test"
    with pytest.raises(ConfigurationError, match="no host named 'evil'"):
        plan_raw(vendor, "GET", "/x", query=None, body=None, headers=None, host="evil")
    caller.close()


def test_a_named_host_follows_the_base_url_rules() -> None:
    from pydantic import ValidationError

    from tests.vendors.data import _endpoint

    with pytest.raises(ValidationError, match="must be https"):
        _vendor(hosts={"bulk": "http://bulk.querykey.test"})
    with pytest.raises(ValidationError, match="not one of the vendor's hosts"):
        _vendor(endpoints=[_endpoint("querykey.x.y", "GET", "/x", host="nope")])


def test_a_path_input_is_required_even_when_the_entry_forgets_to_say_so(catalog: VendorCatalog) -> None:
    from tests.vendors.data import ACME, _endpoint
    from web_router.vendors.schema import Vendor

    vendor = Vendor.model_validate(
        {**ACME, "endpoints": [_endpoint("acme.thing.get", "GET", "/v1/thing/{id}", params={"id": {"in": "path"}})]}
    )
    with pytest.raises(MissingParameterError) as raised:
        plan_endpoint(vendor, vendor.endpoints[0], {})
    assert raised.value.parameters == ["id"]
