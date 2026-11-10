"""Sending vendor calls: where inputs go, and what comes back.

Beside ``test_caller_auth.py`` (each auth shape), ``test_caller_hosts.py``
(body credentials, renamed client ids, named hosts) and
``test_caller_guarantees.py`` (the three guarantees).
"""

from __future__ import annotations

import httpx
import pytest

from tests.vendors.conftest import MakeCaller, Recorder, _call
from web_router.exceptions import MissingParameterError, UnsupportedParameterError
from web_router.vendors.catalog import VendorCatalog
from web_router.vendors.plan import plan_endpoint

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
