"""Sending vendor calls: where inputs go, how each auth shape is applied, and the three guarantees."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tests.vendors.conftest import CREDENTIALS, Recorder
from web_router.exceptions import (
    ConfigurationError,
    MissingParameterError,
    ProviderError,
    UnsupportedParameterError,
    VendorWriteRefusedError,
)
from web_router.vendors.caller import VendorCaller, plan_endpoint, plan_raw
from web_router.vendors.catalog import VendorCatalog
from web_router.vendors.credentials import EnvCredentials, MappingCredentials

MakeCaller = Callable[..., VendorCaller]


def _call(caller: VendorCaller, catalog: VendorCatalog, endpoint: str, params: dict[str, Any], **kw: Any) -> Any:
    found = catalog.endpoint(endpoint)
    return caller.send(plan_endpoint(catalog.vendor(found.vendor), found, params), **kw)


# -- placing inputs ---------------------------------------------------------


def test_query_inputs_are_rendered_and_repeated(catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller):
    params = {"query": "avg:cpu", "from": 100, "tags": ["a", "b"], "rollup": True, "filter": {"k": 1}}
    _call(make_caller(recorder), catalog, "acme.metrics.query", params)

    sent = recorder.last.url
    assert sent.host == "api.us.acme.test"
    assert sent.path == "/v1/query"
    assert sent.params.get_list("tags") == ["a", "b"]
    assert sent.params["rollup"] == "true"
    assert sent.params["from"] == "100"
    assert sent.params["filter"] == '{"k":1}'


def test_body_header_and_wire_names(catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller) -> None:
    _call(
        make_caller(recorder),
        catalog,
        "acme.logs.search",
        {"filter": {"query": "status:error"}, "cursor": "abc", "trace": "t-1"},
    )

    assert recorder.last.method == "POST"
    assert recorder.body() == {"filter": {"query": "status:error"}}
    assert recorder.last.url.params["page[cursor]"] == "abc"
    assert recorder.last.headers["X-Trace"] == "t-1"


def test_path_inputs_are_escaped(catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller) -> None:
    _call(make_caller(recorder), catalog, "acme.monitors.get", {"monitor_id": "a/../b?x=1"})

    assert recorder.last.url.raw_path == b"/v1/monitor/a%2F..%2Fb%3Fx%3D1"
    assert recorder.last.url.host == "api.us.acme.test"


def test_none_inputs_are_not_sent(catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller) -> None:
    _call(make_caller(recorder), catalog, "acme.metrics.query", {"query": "q", "from": 1, "tags": None})

    assert "tags" not in recorder.last.url.params


def test_an_undeclared_input_is_refused_before_sending(catalog: VendorCatalog) -> None:
    endpoint = catalog.endpoint("acme.metrics.query")
    with pytest.raises(UnsupportedParameterError, match="takes: filter, from, query, rollup, tags"):
        plan_endpoint(catalog.vendor("acme"), endpoint, {"query": "q", "from": 1, "country": "US"})


def test_a_missing_required_input_is_refused(catalog: VendorCatalog) -> None:
    endpoint = catalog.endpoint("acme.metrics.query")
    with pytest.raises(MissingParameterError) as raised:
        plan_endpoint(catalog.vendor("acme"), endpoint, {"query": "q"})
    assert raised.value.parameters == ["from"]


# -- auth shapes ------------------------------------------------------------


def test_header_credentials_settings_and_vendor_headers(
    catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller
) -> None:
    _call(make_caller(recorder), catalog, "acme.metrics.query", {"query": "q", "from": 1})

    headers = recorder.last.headers
    assert headers["X-Api-Key"] == "acme-api-secret"
    assert headers["X-App-Key"] == "acme-app-secret"
    assert headers["Acme-Version"] == "2026-01"
    assert "X-Org" not in headers  # optional, and the setting is unset


def test_an_optional_injection_is_sent_when_its_value_is_set(
    catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller
) -> None:
    credentials = {"acme": {**CREDENTIALS["acme"], "org": "org-7", "region": "eu"}}
    _call(make_caller(recorder, credentials), catalog, "acme.metrics.query", {"query": "q", "from": 1})

    assert recorder.last.headers["X-Org"] == "org-7"
    assert recorder.last.url.host == "api.eu.acme.test"


def test_query_credential_is_sent_and_masked_in_the_response_url(
    catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller
) -> None:
    response = _call(make_caller(recorder), catalog, "querykey.domain.overview", {"domain": "example.com"})

    assert recorder.last.url.params["api_key"] == "query-key-secret"
    assert "query-key-secret" not in response.url
    assert "api_key=%2A%2A%2A" in response.url or "api_key=***" in response.url


def test_a_caller_cannot_override_the_query_credential(catalog: VendorCatalog, recorder: Recorder, make_caller):
    plan = plan_raw(catalog.vendor("querykey"), "GET", "/x", query={"api_key": "attacker"}, body=None, headers=None)
    make_caller(recorder).send(plan)

    assert recorder.last.url.params.get_list("api_key") == ["query-key-secret"]


def test_basic_auth(catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller) -> None:
    _call(make_caller(recorder), catalog, "basicco.account.get", {})

    expected = base64.b64encode(b"user:basic-password-secret").decode()
    assert recorder.last.headers["Authorization"] == f"Basic {expected}"


def test_oauth_token_is_minted_once_and_reused(catalog: VendorCatalog, recorder: Recorder, make_caller) -> None:
    caller = make_caller(recorder)
    for _ in range(3):
        _call(caller, catalog, "oauthco.report.query", {"customer_id": "1", "query": "SELECT x"})

    assert len(recorder.token_calls) == 1
    form = dict(httpx.QueryParams(recorder.token_calls[0].content.decode()))
    assert form == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-secret-abc",
        "client_id": "client-123",
        "client_secret": "client-secret-xyz",
    }
    assert recorder.last.headers["Authorization"] == "Bearer minted-access-token"
    assert recorder.last.headers["developer-token"] == "dev-token-secret"
    assert recorder.last.url.path == "/v3/customers/1/search"


def test_a_failed_token_refresh_masks_the_secrets(catalog: VendorCatalog, make_caller: MakeCaller) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    class BadToken(Recorder):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(400, text="invalid_grant for refresh-secret-abc / client-secret-xyz")

    with pytest.raises(ProviderError) as raised:
        _call(make_caller(BadToken(respond)), catalog, "oauthco.report.query", {"customer_id": "1", "query": "q"})
    message = str(raised.value)
    assert "invalid_grant" in message
    assert "refresh-secret-abc" not in message
    assert "client-secret-xyz" not in message


def test_a_keyless_vendor_sends_nothing_extra(catalog: VendorCatalog, recorder: Recorder, make_caller) -> None:
    _call(make_caller(recorder, {}), catalog, "openco.items.list", {})

    assert "authorization" not in recorder.last.headers


def test_a_missing_credential_names_the_variables(catalog: VendorCatalog, recorder: Recorder, make_caller) -> None:
    with pytest.raises(ConfigurationError, match="ACME_APP_KEY or ACME_APPLICATION_KEY"):
        _call(
            make_caller(recorder, {"acme": {"api_key": "k"}}), catalog, "acme.metrics.query", {"query": "q", "from": 1}
        )
    assert recorder.requests == []


def test_credentials_come_from_the_environment(
    catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACME_API_KEY", "from-env")
    monkeypatch.setenv("ACME_APPLICATION_KEY", "second-choice")
    _call(make_caller(recorder, None), catalog, "acme.metrics.query", {"query": "q", "from": 1})

    assert recorder.last.headers["X-Api-Key"] == "from-env"
    assert recorder.last.headers["X-App-Key"] == "second-choice"


def test_passed_credentials_win_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACME_API_KEY", "from-env")
    from web_router.vendors.credentials import credential_source

    source = credential_source({"acme": {"api_key": "passed"}})
    assert source.get("acme", "api_key", ["ACME_API_KEY"]) == "passed"
    assert source.get("acme", "app_key", ["ACME_API_KEY"]) == "from-env"
    assert MappingCredentials({}).get("acme", "x", []) is None
    assert EnvCredentials().get("acme", "x", ["UNSET_VAR_FOR_TEST"]) is None


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


# -- the answer -------------------------------------------------------------


def test_json_and_text_answers(catalog: VendorCatalog, make_caller: MakeCaller) -> None:
    recorder = Recorder(lambda request: httpx.Response(200, json={"series": [1]}, headers={"X-RateLimit": "9"}))
    response = _call(make_caller(recorder), catalog, "acme.metrics.query", {"query": "q", "from": 1})
    assert response.data == {"series": [1]}
    assert response.text is None
    assert response.headers["x-ratelimit"] == "9"
    assert response.endpoint == "acme.metrics.query"

    recorder = Recorder(lambda request: httpx.Response(200, text="a,b\n1,2", headers={"Set-Cookie": "s=1"}))
    response = _call(make_caller(recorder), catalog, "acme.metrics.query", {"query": "q", "from": 1})
    assert response.data is None
    assert response.text == "a,b\n1,2"
    assert "set-cookie" not in response.headers


async def test_the_async_path(catalog: VendorCatalog, recorder: Recorder, make_caller: MakeCaller) -> None:
    caller = make_caller(recorder)
    found = catalog.endpoint("oauthco.report.query")
    plan = plan_endpoint(catalog.vendor("oauthco"), found, {"customer_id": "5", "query": "q"})
    response = await caller.asend(plan)
    await caller.asend(plan)
    await caller.aclose()

    assert response.status_code == 200
    assert len(recorder.token_calls) == 1
    assert recorder.last.headers["Authorization"] == "Bearer minted-access-token"


# -- format additions: body credentials, renamed client id, named hosts -----


def _vendor(**changes: Any) -> Any:
    from tests.vendors.conftest import QUERYKEY
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
    from tests.vendors.conftest import OAUTH
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
    from tests.vendors.conftest import _endpoint

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

    from tests.vendors.conftest import _endpoint

    with pytest.raises(ValidationError, match="must be https"):
        _vendor(hosts={"bulk": "http://bulk.querykey.test"})
    with pytest.raises(ValidationError, match="not one of the vendor's hosts"):
        _vendor(endpoints=[_endpoint("querykey.x.y", "GET", "/x", host="nope")])
