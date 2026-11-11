"""Which credentials a call carries: only the endpoint's own, a token only when it needs one, none echoed back."""

from __future__ import annotations

from contextlib import closing
from typing import Any

import httpx

from tests.vendors.conftest import Recorder
from tests.vendors.data import ACME, CREDENTIALS, OAUTH, _endpoint
from web_router.vendors.caller import VendorCaller
from web_router.vendors.catalog import VendorCatalog
from web_router.vendors.masking import mask_answer
from web_router.vendors.plan import plan_endpoint
from web_router.vendors.schema import Vendor

_KEY_ONLY = _endpoint("oauthco.account.get", "GET", "/account", credentials=["developer_token"])
_KEY_ONLY_ACME = _endpoint("acme.auth.validate", "GET", "/v1/validate", credentials=["api_key"])
_EVERY_KEY_ACME = _endpoint("acme.status.get", "GET", "/v1/status")


def _caller(vendors: list[dict[str, Any]], recorder: Recorder, credentials: Any) -> tuple[VendorCaller, VendorCatalog]:
    catalog = VendorCatalog(Vendor.model_validate(vendor) for vendor in vendors)
    caller = VendorCaller(credentials, catalog=catalog, transport=httpx.MockTransport(recorder))
    return caller, catalog


def _send(caller: VendorCaller, catalog: VendorCatalog, endpoint: str, params: dict[str, Any] | None = None) -> Any:
    found = catalog.endpoint(endpoint)
    return caller.send(plan_endpoint(catalog.vendor(found.vendor), found, params or {}))


def test_a_key_only_endpoint_on_an_oauth_vendor_mints_no_token() -> None:
    vendor = {**OAUTH, "endpoints": [*OAUTH["endpoints"], _KEY_ONLY]}
    recorder = Recorder()
    caller, catalog = _caller([vendor], recorder, {"oauthco": {"developer_token": "dev-token-secret"}})
    with closing(caller):
        _send(caller, catalog, "oauthco.account.get")
    assert recorder.token_calls == []
    assert "authorization" not in recorder.last.headers
    assert recorder.last.headers["developer-token"] == "dev-token-secret"


def test_with_every_credential_set_only_the_oauth_endpoint_mints() -> None:
    vendor = {**OAUTH, "endpoints": [*OAUTH["endpoints"], _KEY_ONLY]}
    recorder = Recorder()
    caller, catalog = _caller([vendor], recorder, CREDENTIALS)
    with closing(caller):
        _send(caller, catalog, "oauthco.account.get")
        assert recorder.token_calls == []
        _send(caller, catalog, "oauthco.report.query", {"customer_id": "1", "query": "q"})
    assert len(recorder.token_calls) == 1
    assert recorder.last.headers["authorization"] == "Bearer minted-access-token"


def test_a_credential_the_endpoint_leaves_out_is_not_sent() -> None:
    vendor = {**ACME, "endpoints": [_KEY_ONLY_ACME, _EVERY_KEY_ACME]}
    recorder = Recorder()
    caller, catalog = _caller([vendor], recorder, CREDENTIALS)
    with closing(caller):
        _send(caller, catalog, "acme.auth.validate")
        assert recorder.last.headers["x-api-key"] == "acme-api-secret"
        assert "x-app-key" not in recorder.last.headers
        _send(caller, catalog, "acme.status.get")
        assert recorder.last.headers["x-app-key"] == "acme-app-secret"


def test_a_secret_echoed_into_the_content_type_is_masked() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        echoed = "application/json; x-key=acme-api-secret"
        return httpx.Response(200, content=b'{"ok": true}', headers={"content-type": echoed})

    caller, catalog = _caller([{**ACME, "endpoints": [_EVERY_KEY_ACME]}], Recorder(respond), CREDENTIALS)
    with closing(caller):
        response = _send(caller, catalog, "acme.status.get")
    assert response.content_type == "application/json; x-key=***"
    assert response.data == {"ok": True}


def test_a_plain_or_missing_content_type_is_unchanged() -> None:
    caller, catalog = _caller([{**ACME, "endpoints": [_EVERY_KEY_ACME]}], Recorder(), CREDENTIALS)
    with closing(caller):
        assert _send(caller, catalog, "acme.status.get").content_type == "application/json"
    bare = Recorder(lambda request: httpx.Response(204))
    caller, catalog = _caller([{**ACME, "endpoints": [_EVERY_KEY_ACME]}], bare, CREDENTIALS)
    with closing(caller):
        assert _send(caller, catalog, "acme.status.get").content_type is None


def test_a_short_secret_is_masked_as_a_whole_word_inside_an_answer() -> None:
    answer = {"auth": "Bearer abc123", "field": "username", "note": "a user here"}
    masked = mask_answer(answer, ["abc123", "user"])
    assert masked == {"auth": "Bearer ***", "field": "username", "note": "a *** here"}
