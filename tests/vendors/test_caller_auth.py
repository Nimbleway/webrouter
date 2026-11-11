"""Sending vendor calls: how each auth shape is applied, and where credentials come from."""

from __future__ import annotations

import base64

import httpx
import pytest

from tests.vendors.conftest import MakeCaller, Recorder, _call
from tests.vendors.data import CREDENTIALS
from web_router.exceptions import ConfigurationError, ProviderError
from web_router.vendors.catalog import VendorCatalog
from web_router.vendors.credentials import EnvCredentials, MappingCredentials
from web_router.vendors.plan import plan_raw

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
