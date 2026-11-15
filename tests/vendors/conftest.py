"""The vendor tests' fixtures: a catalog of the made-up vendors in :mod:`tests.vendors.data`, and a
transport that records what was sent."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from tests.vendors.data import ALL_VENDORS, CREDENTIALS
from web_router.vendors.caller import VendorCaller
from web_router.vendors.catalog import VendorCatalog
from web_router.vendors.plan import plan_endpoint
from web_router.vendors.schema import Vendor

MakeCaller = Callable[..., VendorCaller]


def _call(caller: VendorCaller, catalog: VendorCatalog, endpoint: str, params: dict[str, Any], **kw: Any) -> Any:
    """Send ``endpoint`` with ``params`` through ``caller``, planned from ``catalog``."""
    found = catalog.endpoint(endpoint)
    return caller.send(plan_endpoint(catalog.vendor(found.vendor), found, params), **kw)


class Recorder:
    """An httpx transport answering from a function, keeping every request it was sent."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond or (lambda request: httpx.Response(200, json={"ok": True}))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "auth.oauth.test":
            return httpx.Response(200, json={"access_token": "minted-access-token", "expires_in": 3600})
        return self._respond(request)

    @property
    def last(self) -> httpx.Request:
        return [r for r in self.requests if r.url.host != "auth.oauth.test"][-1]

    def body(self) -> Any:
        return json.loads(self.last.content)

    @property
    def token_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "auth.oauth.test"]


@pytest.fixture
def catalog() -> VendorCatalog:
    return VendorCatalog(Vendor.model_validate(vendor) for vendor in ALL_VENDORS)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def make_caller(catalog: VendorCatalog) -> Iterator[Callable[..., VendorCaller]]:
    made: list[VendorCaller] = []

    def make(recorder: Recorder, credentials: Any = CREDENTIALS) -> VendorCaller:
        caller = VendorCaller(
            credentials,
            catalog=catalog,
            transport=httpx.MockTransport(recorder),
            async_transport=httpx.MockTransport(recorder),
        )
        made.append(caller)
        return caller

    yield make
    for caller in made:
        caller.close()


@pytest.fixture(autouse=True)
def _no_vendor_env(monkeypatch: pytest.MonkeyPatch, catalog: VendorCatalog) -> None:
    """No vendor variable from this machine reaches a test: every value comes from the test itself."""
    from web_router.vendors.catalog import get_catalog

    for vendor in [*catalog.all(), *get_catalog().all()]:
        for variable in vendor.credential_variables():
            monkeypatch.delenv(variable, raising=False)


ADS_BUDGET_MUTATE = {
    "id": "google-ads.campaign_budgets.mutate",
    "name": "Create, change or remove campaign budgets",
    "summary": "Change a campaign's daily budget, among other budget operations.",
    "kind": "write",
    "method": "POST",
    "path": "/customers/{customer_id}/campaignBudgets:mutate",
    "params": {
        "customer_id": {"in": "path", "type": "string", "required": True},
        "operations": {"in": "body", "type": "array", "required": True},
        "validateOnly": {"in": "body", "type": "boolean"},
    },
    "job": "ads.budgets.update",
    "docs_url": "https://developers.google.com/google-ads/api/rest/examples",
    "verified": {"docs": "https://developers.google.com/google-ads/api/reference/rpc/v26/MutateCampaignBudgetsRequest"},
}


@pytest.fixture
def ads_with_a_write(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The built-in catalog only reads: a user's own catalog directory gives Google Ads a write to gate."""
    from web_router.constants_vendors import VENDOR_ENV_CATALOG_DIR
    from web_router.vendors.catalog import builtin_catalog

    ads = builtin_catalog().vendor("google-ads").model_dump(by_alias=True, exclude_none=True)
    ads["endpoints"] = [*ads["endpoints"], ADS_BUDGET_MUTATE]
    (tmp_path / "google-ads.json").write_text(json.dumps(ads))
    monkeypatch.setenv(VENDOR_ENV_CATALOG_DIR, str(tmp_path))
