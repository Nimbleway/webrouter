"""The caller's guarantees: writes are opt-in, credentials stay on the vendor's host, and never leak back.

Beside ``test_caller.py``, which covers where inputs go, and ``test_caller_auth.py``,
how each auth shape is applied; the token failures are in ``test_tokens.py``.
"""

from __future__ import annotations

import httpx
import pytest

from tests.vendors.conftest import MakeCaller, Recorder, _call
from tests.vendors.data import CREDENTIALS
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.exceptions_vendors import VendorWriteRefusedError
from web_router.vendors.caller import VendorCaller
from web_router.vendors.catalog import VendorCatalog
from web_router.vendors.masking import mask, mask_answer
from web_router.vendors.plan import plan_endpoint, plan_raw

# -- guarantees -------------------------------------------------------------


def test_a_write_is_refused_without_allow_writes(catalog: VendorCatalog, recorder: Recorder, make_caller) -> None:
    with pytest.raises(VendorWriteRefusedError, match=r"acme\.monitors\.delete"):
        _call(make_caller(recorder), catalog, "acme.monitors.delete", {"monitor_id": "9"})
    assert recorder.requests == []

    _call(make_caller(recorder), catalog, "acme.monitors.delete", {"monitor_id": "9"}, allow_writes=True)
    assert recorder.last.method == "DELETE"


def test_a_refused_write_mints_no_token(catalog: VendorCatalog, recorder: Recorder, make_caller) -> None:
    plan = plan_raw(catalog.vendor("oauthco"), "POST", "/x", query=None, body={}, headers=None)
    with pytest.raises(VendorWriteRefusedError):
        make_caller(recorder).send(plan)
    assert recorder.requests == []


def test_a_read_over_post_in_the_catalog_needs_no_allow_writes(
    catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller
) -> None:
    _call(make_caller(recorder), catalog, "acme.logs.search", {"filter": {}})
    assert recorder.last.method == "POST"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_a_raw_non_read_method_needs_allow_writes(catalog: VendorCatalog, recorder: Recorder, make_caller, method):
    plan = plan_raw(catalog.vendor("acme"), method, "/v1/x", query=None, body={"a": 1}, headers=None)
    with pytest.raises(VendorWriteRefusedError):
        make_caller(recorder).send(plan)


@pytest.mark.parametrize("path", ["https://evil.test/x", "//evil.test/x", "x", "/a\\b", "http://api.us.acme.test/v1"])
def test_a_raw_path_cannot_name_a_host(catalog: VendorCatalog, path: str) -> None:
    with pytest.raises(ConfigurationError, match="Credentials are only ever sent"):
        plan_raw(catalog.vendor("acme"), "GET", path, query=None, body=None, headers=None)


def test_a_userinfo_trick_stays_on_the_vendor_host(catalog: VendorCatalog, recorder: Recorder, make_caller) -> None:
    plan = plan_raw(catalog.vendor("acme"), "GET", "/@evil.test/x", query=None, body=None, headers=None)
    make_caller(recorder).send(plan)
    assert recorder.last.url.host == "api.us.acme.test"


def test_a_region_outside_its_choices_is_refused(catalog: VendorCatalog, recorder: Recorder, make_caller) -> None:
    credentials = {"acme": {**CREDENTIALS["acme"], "region": "evil.test/#"}}
    with pytest.raises(ConfigurationError, match="must be one of us, eu"):
        _call(make_caller(recorder, credentials), catalog, "acme.metrics.query", {"query": "q", "from": 1})
    assert recorder.requests == []


def test_redirects_are_not_followed(catalog: VendorCatalog, make_caller: MakeCaller) -> None:
    recorder = Recorder(lambda request: httpx.Response(302, headers={"Location": "https://evil.test/steal"}))
    response = _call(make_caller(recorder), catalog, "acme.metrics.query", {"query": "q", "from": 1})

    assert response.status_code == 302
    assert all(request.url.host != "evil.test" for request in recorder.requests)


def test_an_error_body_has_every_credential_masked(catalog: VendorCatalog, make_caller: MakeCaller) -> None:
    echo = "bad key acme-api-secret, url api_key=query-key-secret and acme-app-secret"
    recorder = Recorder(lambda request: httpx.Response(403, text=echo))
    with pytest.raises(ProviderError) as raised:
        _call(make_caller(recorder), catalog, "acme.metrics.query", {"query": "q", "from": 1})
    message = str(raised.value)
    assert "HTTP 403 from GET /v1/query" in message
    assert "acme-api-secret" not in message
    assert "acme-app-secret" not in message


def test_a_url_encoded_credential_is_masked_too(catalog: VendorCatalog, make_caller: MakeCaller) -> None:
    credentials = {"querykey": {"key": "k+y/with spaces"}}
    recorder = Recorder(lambda request: httpx.Response(401, text=f"rejected {request.url}"))
    with pytest.raises(ProviderError) as raised:
        _call(make_caller(recorder, credentials), catalog, "querykey.domain.overview", {})
    assert "with" not in str(raised.value).split("rejected", 1)[1]


def test_a_transport_failure_is_a_provider_error(catalog: VendorCatalog, make_caller: MakeCaller) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom acme-api-secret", request=request)

    with pytest.raises(ProviderError) as raised:
        _call(make_caller(Recorder(fail)), catalog, "acme.metrics.query", {"query": "q", "from": 1})
    assert "acme-api-secret" not in str(raised.value)


# -- review fixes: secrets in answers, short keys, per-call credentials ------


def test_a_secret_echoed_in_a_successful_answer_is_masked(catalog: VendorCatalog, make_caller: MakeCaller) -> None:
    echo = {"key": "acme-api-secret", "note": "called with acme-app-secret", "username": "someone"}
    recorder = Recorder(lambda request: httpx.Response(200, json=echo, headers={"X-Echo": "acme-api-secret"}))
    response = _call(make_caller(recorder), catalog, "acme.metrics.query", {"query": "q", "from": 1})

    assert response.data == {"key": "***", "note": "called with ***", "username": "someone"}
    assert response.headers["x-echo"] == "***"


def test_a_short_secret_is_masked_as_a_word_but_not_inside_other_words() -> None:
    answer = {"login": "user", "field": "username", "list": ["user", "a user here"]}
    assert mask_answer(answer, ["user"]) == {"login": "***", "field": "username", "list": ["***", "a *** here"]}
    assert mask("rejected key ab1 for this call", ["ab1"]) == "rejected key *** for this call"


def test_an_endpoint_can_need_fewer_credentials_than_its_vendor(
    catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller
) -> None:
    from tests.vendors.data import ACME, _endpoint
    from web_router.vendors.schema import Vendor

    vendor = Vendor.model_validate(
        {**ACME, "endpoints": [_endpoint("acme.auth.validate", "GET", "/v1/validate", credentials=["api_key"])]}
    )
    caller = VendorCaller(
        {"acme": {"api_key": "only-the-api-key"}},
        catalog=VendorCatalog([vendor]),
        transport=httpx.MockTransport(recorder),
    )
    caller.send(plan_endpoint(vendor, vendor.endpoints[0], {}))
    assert recorder.last.headers["X-Api-Key"] == "only-the-api-key"
    assert "X-App-Key" not in recorder.last.headers
    caller.close()


def test_the_shipped_datadog_key_check_needs_only_the_api_key(recorder: Recorder) -> None:
    from web_router.vendors.catalog import get_catalog

    shipped = get_catalog()
    caller = VendorCaller({"datadog": {"api_key": "dd-api-only"}}, transport=httpx.MockTransport(recorder))
    found = shipped.endpoint("datadog.auth.validate")
    caller.send(plan_endpoint(shipped.vendor("datadog"), found, {}))
    assert recorder.last.headers["DD-API-KEY"] == "dd-api-only"
    assert "DD-APPLICATION-KEY" not in recorder.last.headers
    with pytest.raises(ConfigurationError, match="DD_APPLICATION_KEY"):
        found = shipped.endpoint("datadog.monitors.list")
        caller.send(plan_endpoint(shipped.vendor("datadog"), found, {}))
    caller.close()
