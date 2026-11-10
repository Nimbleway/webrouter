"""The vendor methods on WebRouter and AsyncWebRouter, against the shipped catalog."""

from __future__ import annotations

import httpx
import pytest

from tests.vendors.conftest import Recorder
from web_router import AsyncWebRouter, WebRouter
from web_router.exceptions_vendors import VendorNotFoundError, VendorWriteRefusedError
from web_router.vendors.caller import VendorCaller
from web_router.vendors.catalog import get_catalog

DATADOG = {"datadog": {"api_key": "dd-api", "app_key": "dd-app"}}


def _wire(router: WebRouter | AsyncWebRouter, recorder: Recorder) -> None:
    router._vendor_caller = VendorCaller(
        router.vendor_credential_source(),
        transport=httpx.MockTransport(recorder),
        async_transport=httpx.MockTransport(recorder),
    )


def test_vendors_lists_the_catalog_with_what_is_missing() -> None:
    with WebRouter(vendor_credentials=DATADOG) as router:
        rows = {row.vendor: row for row in router.vendors()}
        assert rows["datadog"].configured
        assert not rows["google-ads"].configured
        assert "GOOGLE_ADS_DEVELOPER_TOKEN" in rows["google-ads"].missing
        configured = {row.vendor for row in router.vendors(configured=True)}
        assert "datadog" in configured
        assert "google-ads" not in configured
        # The rest of the configured list is vendors that need no key at all.
        keyless = {row.vendor for row in router.vendors() if not router.vendor(row.vendor).auth.credentials}
        keyless |= {
            row.vendor
            for row in router.vendors()
            if all(not spec.required for spec in router.vendor(row.vendor).auth.credentials.values())
        }
        assert configured - {"datadog"} <= keyless
        assert all(row.category == "advertising" for row in router.vendors(category="advertising"))


def test_vendors_filter_by_whose_data_they_hold() -> None:
    with WebRouter() as router:
        public = {row.vendor for row in router.vendors(data="public")}
        account = {row.vendor for row in router.vendors(data="account")}
        service = {row.vendor for row in router.vendors(data="service")}
    assert {"dataforseo", "crunchbase", "hunter"} <= public
    assert {"datadog", "google-ads", "slack"} <= account
    assert {"openai", "twilio", "elevenlabs"} <= service
    assert len(public) + len(account) + len(service) == len(get_catalog())


def test_describe_and_find() -> None:
    with WebRouter() as router:
        assert router.vendor("google-ads").name == "Google Ads"
        assert router.vendor_endpoint("datadog.logs.search").method == "POST"
        found = [endpoint.id for endpoint in router.find_vendor_endpoints("datadog error logs")]
        assert found[0] == "datadog.logs.search"
        reads = router.find_vendor_endpoints("google ads campaigns", kind="read")
        assert all(endpoint.kind == "read" for endpoint in reads)
        with pytest.raises(VendorNotFoundError):
            router.vendor_endpoint("datadog.nope")


def test_call_vendor_sends_to_the_vendor() -> None:
    recorder = Recorder()
    with WebRouter(vendor_credentials={**DATADOG, "datadog": {**DATADOG["datadog"], "site": "datadoghq.eu"}}) as router:
        _wire(router, recorder)
        response = router.call_vendor("datadog.monitors.search", {"query": "status:alert", "per_page": 5})

    assert response.data == {"ok": True}
    assert str(recorder.last.url) == "https://api.datadoghq.eu/api/v1/monitor/search?query=status%3Aalert&per_page=5"
    assert recorder.last.headers["DD-API-KEY"] == "dd-api"
    assert recorder.last.headers["DD-APPLICATION-KEY"] == "dd-app"


def test_vendor_request_reaches_paths_the_catalog_does_not_name() -> None:
    recorder = Recorder()
    with WebRouter(vendor_credentials=DATADOG) as router:
        _wire(router, recorder)
        router.vendor_request("datadog", "GET", "/api/v2/users", query={"page[size]": 2})
        with pytest.raises(VendorWriteRefusedError):
            router.vendor_request("datadog", "DELETE", "/api/v1/monitor/1")

    assert recorder.last.url.path == "/api/v2/users"
    assert recorder.last.url.params["page[size]"] == "2"


def test_google_ads_writes_are_gated_and_reads_pass() -> None:
    credentials = {
        "google-ads": {
            "developer_token": "dev",
            "client_id": "cid",
            "client_secret": "cs",
            "refresh_token": "rt",
            "login_customer_id": "9998887777",
        }
    }

    class GoogleToken(Recorder):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.host == "oauth2.googleapis.com":
                self.requests.append(request)
                return httpx.Response(200, json={"access_token": "ya29.x", "expires_in": 3599})
            return super().__call__(request)

        @property
        def last(self) -> httpx.Request:
            return [r for r in self.requests if r.url.host != "oauth2.googleapis.com"][-1]

    recorder = GoogleToken()
    with WebRouter(vendor_credentials=credentials) as router:
        _wire(router, recorder)
        router.call_vendor(
            "google-ads.query", {"customer_id": "1234567890", "query": "SELECT campaign.name FROM campaign"}
        )
        sent = recorder.last
        assert str(sent.url) == "https://googleads.googleapis.com/v26/customers/1234567890/googleAds:search"
        assert sent.headers["Authorization"] == "Bearer ya29.x"
        assert sent.headers["developer-token"] == "dev"
        assert sent.headers["login-customer-id"] == "9998887777"

        budget = {"customer_id": "1234567890", "operations": [], "validateOnly": True}
        with pytest.raises(VendorWriteRefusedError):
            router.call_vendor("google-ads.campaign_budgets.mutate", budget)
        router.call_vendor("google-ads.campaign_budgets.mutate", budget, allow_writes=True)
        assert recorder.last.url.path.endswith("/campaignBudgets:mutate")


async def test_async_router() -> None:
    recorder = Recorder()
    async with AsyncWebRouter(vendor_credentials=DATADOG) as router:
        _wire(router, recorder)
        response = await router.call_vendor("datadog.auth.validate")
        raw = await router.vendor_request("datadog", "GET", "/api/v1/validate")

    assert response.status_code == raw.status_code == 200
    assert recorder.last.url.host == "api.datadoghq.com"
