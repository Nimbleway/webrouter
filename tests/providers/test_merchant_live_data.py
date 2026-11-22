"""``merchant`` serves a store's own agent interface as a live data source, routed by the registry record."""

from __future__ import annotations

import pytest

from tests._merchant_fake import (
    DOMAIN,
    ENDPOINT,
    PRODUCT,
    PROFILE,
    REGISTRY,
    SNAPSHOT,
    Merchant,
    pointer,
    record,
    router_config,
)
from tests.live_data_env_helpers import clear_live_data_credentials
from web_router import AsyncWebRouter, ConfigurationError, LiveDataSourceNotFoundError, ProviderError, WebRouter
from web_router.exceptions_merchant import MerchantNotValidatedError, MerchantRouteError
from web_router.providers.merchant_live_data import MerchantLiveDataProvider


def ask(merchant: Merchant, query: str = "shoe", source: str = DOMAIN, options: dict | None = None, **inputs: object):  # type: ignore[type-arg]
    with WebRouter("nimble", provider_config=router_config(merchant)) as router:
        return router.live_data(source, {"query": query, **inputs}, provider="merchant", provider_options=options)  # type: ignore[arg-type]


def test_it_reads_the_registry_then_makes_one_store_call_and_returns_the_stores_products_untouched() -> None:
    merchant = Merchant()
    reply = ask(merchant, "shoe", limit=5)
    assert [rpc for *_, rpc in merchant.seen] == [None, None, "initialize", "notifications/initialized", "tools/call"]
    assert reply.records == [PRODUCT] and reply.provider == "merchant" and reply.source == DOMAIN
    assert reply.url == ENDPOINT and reply.fetched_at is None, "no fetch time is invented"
    assert reply.extra["snapshot_id"] == SNAPSHOT and reply.extra["evidence"]["search_keyword"] == "pass"
    assert reply.extra["production_ready"] is False
    call = merchant.tool_call()
    assert call["name"] == "search_catalog"
    assert call["arguments"]["catalog"] == {"query": "shoe", "pagination": {"limit": 5}}
    assert call["arguments"]["meta"] == {"ucp-agent": {"profile": PROFILE}}


def test_it_never_asks_the_store_what_it_offers() -> None:
    merchant = Merchant()
    ask(merchant)
    assert "tools/list" not in merchant.store_calls()


def test_the_session_and_the_version_the_store_agreed_to_are_the_ones_the_search_runs_in() -> None:
    merchant = Merchant()
    reply = ask(merchant)
    search_headers = merchant.headers[-1]
    assert search_headers["mcp-session-id"] == "s-1" and search_headers["mcp-protocol-version"] == "2025-11-25"
    assert reply.extra["protocol_version"] == "2025-11-25", "read from the initialize reply"


def test_the_pointer_and_the_records_are_cached_across_calls() -> None:
    merchant = Merchant()
    with WebRouter("nimble", provider_config=router_config(merchant)) as router:
        router.live_data(DOMAIN, {"query": "a"}, provider="merchant")
        router.live_data(DOMAIN, {"query": "b"}, provider="merchant")
    registry_reads = [url for _, url, _ in merchant.seen if url.startswith("https://registry.test/")]
    assert len(registry_reads) == 2, "one pointer read and one record read for two calls"


def test_a_reply_sent_as_an_event_stream_is_read() -> None:
    assert ask(Merchant(sse=True)).records == [PRODUCT]


def test_a_domain_the_snapshot_does_not_list_is_unknown_not_refused_and_nothing_reaches_a_store() -> None:
    merchant = Merchant(status=404)
    with pytest.raises(LiveDataSourceNotFoundError) as caught:
        ask(merchant)
    assert caught.value.kind == "not_found" and SNAPSHOT in str(caught.value)
    assert merchant.store_calls() == []


def test_an_unpublished_registry_is_not_reported_as_an_unknown_store() -> None:
    merchant = Merchant()
    merchant.latest = None  # type: ignore[assignment]
    with pytest.raises(ProviderError) as caught:
        ask(merchant)
    assert not isinstance(caught.value, LiveDataSourceNotFoundError)


@pytest.mark.parametrize(
    "latest",
    [pointer(schema_version="2.0"), pointer(snapshot_id="../../etc"), {"snapshot_id": SNAPSHOT}, []],
)
def test_a_pointer_it_does_not_accept_stops_before_any_domain_is_fetched(latest: object) -> None:
    merchant = Merchant(latest=latest)  # type: ignore[arg-type]
    with pytest.raises(ProviderError):
        ask(merchant)
    assert len(merchant.seen) == 1


def test_a_record_from_another_snapshot_or_domain_is_rejected() -> None:
    for wrong in (record(snapshot_id="deadbeef00"), record(domain="other.example.com")):
        merchant = Merchant(domain_record=wrong)
        with pytest.raises(ProviderError):
            ask(merchant)
        assert merchant.store_calls() == []


def _route(endpoint: str) -> dict:  # type: ignore[type-arg]
    bad = record(routes=[{"provider": "direct:ucp", "protocol": "mcp", "endpoint": endpoint}])
    bad["commerce_evidence"]["endpoint"] = endpoint
    return bad


@pytest.mark.parametrize(
    "bad",
    [
        record(quarantined=True),
        record(tests={"search_keyword": "empty"}),
        record(routes=[]),
        _route("http://store.myshopify.com/mcp"),
        _route("https://user:pw@store.myshopify.com/mcp"),
        _route("https://:secret@store.myshopify.com/mcp"),
        _route("https://[store.myshopify.com/mcp"),
        _route("https://203.0.113.9/mcp"),
        _route("https://127.0.0.1/mcp"),
        _route("https://localhost/mcp"),
        _route("https://[::1]/mcp"),
        _route("https://attacker.example.net/mcp"),
        _route("https://shop.example.com.attacker.net/mcp"),
    ],
)
def test_a_store_without_a_passing_search_or_a_safe_https_route_is_refused_not_run(bad: dict) -> None:  # type: ignore[type-arg]
    merchant = Merchant(domain_record=bad)
    with pytest.raises(MerchantRouteError):
        ask(merchant)
    assert merchant.store_calls() == []


@pytest.mark.parametrize(
    "endpoint", ["https://shop.example.com/mcp", "https://api.shop.example.com/mcp", "https://x.wixapis.com/ucp"]
)
def test_the_stores_own_hosts_and_the_platforms_that_run_it_are_called(endpoint: str) -> None:
    merchant = Merchant(domain_record=_route(endpoint))
    merchant.transport  # noqa: B018 - the fake answers by URL; only the plan is under test here
    from web_router.providers.merchant_record import plan_for

    assert plan_for(merchant.domain_record, DOMAIN, SNAPSHOT).endpoint == endpoint


def test_a_template_catalogue_is_not_served() -> None:
    bad = record()
    bad["commerce_evidence"]["real_catalogue"] = False
    with pytest.raises(MerchantRouteError, match="template"):
        ask(Merchant(domain_record=bad))


@pytest.mark.parametrize(
    ("option", "value", "status"),
    [
        ("min_price", 1000, "ignored"),
        ("max_price", 5000, "partial"),
        ("country", "DE", "empty"),
        ("currency", "EUR", "empty"),
    ],
)
def test_an_option_the_store_has_no_passing_test_for_is_refused_rather_than_silently_dropped(
    option: str, value: object, status: str
) -> None:
    merchant = Merchant(domain_record=record(tests={"price_filter": status, "localisation": status}))
    with pytest.raises(MerchantNotValidatedError) as caught:
        ask(merchant, **{option: value})
    assert option in caught.value.parameters
    assert merchant.store_calls() == [], "nothing is sent to the store for a call that was refused"


def test_a_price_filter_the_store_honours_is_sent_in_the_stores_own_shape() -> None:
    merchant = Merchant()
    ask(merchant, min_price=1000, max_price=20000)
    assert merchant.tool_call()["arguments"]["catalog"]["filters"] == {"price": {"min": 1000, "max": 20000}}


def test_a_store_that_needs_the_callers_profile_is_not_called_without_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_ROUTER_MERCHANT_AGENT_PROFILE_URL", raising=False)
    merchant = Merchant()
    config = router_config(merchant)
    config["merchant"].pop("agent_profile_url")
    with (
        WebRouter("nimble", provider_config=config) as router,
        pytest.raises(ConfigurationError, match="agent profile"),
    ):
        router.live_data(DOMAIN, {"query": "shoe"}, provider="merchant")
    assert merchant.store_calls() == []


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_agent_profile_counts_as_none_given(blank: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_ROUTER_MERCHANT_AGENT_PROFILE_URL", blank)
    merchant = Merchant()
    config = router_config(merchant)
    config["merchant"]["agent_profile_url"] = blank
    assert MerchantLiveDataProvider(**config["merchant"]).agent_profile_url is None
    with (
        WebRouter("nimble", provider_config=config) as router,
        pytest.raises(ConfigurationError, match="agent profile"),
    ):
        router.live_data(DOMAIN, {"query": "shoe"}, provider="merchant")
    assert merchant.store_calls() == []


def test_a_profile_url_is_trimmed_before_it_is_sent() -> None:
    merchant = Merchant()
    config = router_config(merchant)
    config["merchant"]["agent_profile_url"] = "  https://agent.example/profile.json \n"
    assert MerchantLiveDataProvider(**config["merchant"]).agent_profile_url == "https://agent.example/profile.json"


def test_following_redirects_is_refused_because_the_stores_host_is_checked_only_before_the_call() -> None:
    config = router_config(Merchant())
    with pytest.raises(ConfigurationError, match="follow_redirects"):
        MerchantLiveDataProvider(**config["merchant"], follow_redirects=True)


@pytest.mark.parametrize("reserved", ["catalog", "meta"])
def test_provider_options_cannot_replace_the_gated_catalog_or_the_identity(reserved: str) -> None:
    merchant = Merchant()
    with pytest.raises(ProviderError, match=reserved):
        ask(merchant, options={reserved: {"x": 1}})
    assert merchant.store_calls() == []


def test_another_provider_option_is_passed_to_the_search_beside_the_gated_arguments() -> None:
    merchant = Merchant()
    ask(merchant, options={"extra_flag": True})
    arguments = merchant.tool_call()["arguments"]
    assert arguments["extra_flag"] is True and arguments["catalog"]["query"] == "shoe"


def test_a_store_error_raises_and_is_never_an_empty_result() -> None:
    merchant = Merchant(store_result={"isError": True, "structuredContent": {"messages": [{"code": "SE-1001"}]}})
    with pytest.raises(ProviderError, match="SE-1001"):
        ask(merchant)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_a_store_that_refuses_the_initialized_notice_stops_the_call_before_the_search(asynchronous: bool) -> None:
    merchant = Merchant(notice_status=429)
    if asynchronous:
        import asyncio

        async def run() -> None:
            async with AsyncWebRouter("nimble", provider_config=router_config(merchant)) as router:
                await router.live_data(DOMAIN, {"query": "shoe"}, provider="merchant")

        with pytest.raises(ProviderError, match="HTTP 429"):
            asyncio.run(run())
    else:
        with pytest.raises(ProviderError, match="HTTP 429"):
            ask(merchant)
    assert merchant.store_calls() == ["initialize", "notifications/initialized"], "the search was never sent"


def test_an_event_stream_reads_data_without_a_space_and_a_data_split_over_lines() -> None:
    from web_router.providers.merchant_mcp import _last_event_data

    stream = 'event: message\ndata:{"a": 1}\n\nevent: message\ndata: {"result":\ndata: {"x": 2}}\n\n'
    assert _last_event_data(stream) == '{"result":\n{"x": 2}}'
    assert _last_event_data("event: ping\n\n") == ""


def test_a_stores_429_is_a_rate_limit_not_an_upstream_error() -> None:
    merchant = Merchant(notice_status=429)
    with pytest.raises(ProviderError) as raised:
        ask(merchant)
    assert raised.value.kind == "rate_limit"


def test_the_snapshot_is_read_once_per_call_even_when_the_pointer_expires_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("web_router.providers.merchant_registry.REGISTRY_POINTER_TTL_S", 0)
    merchant = Merchant()
    ask(merchant)
    assert [url for _, url, _ in merchant.seen if url.endswith("latest.json")] == [REGISTRY + "latest.json"], (
        "the record is checked against the snapshot the call started with"
    )


def test_an_old_snapshots_records_and_index_are_dropped_when_the_pointer_moves() -> None:
    merchant = Merchant()
    provider = MerchantLiveDataProvider(agent_profile_url=PROFILE, registry_url=REGISTRY, transport=merchant.transport)
    provider._records[("old-snapshot", DOMAIN)] = {}
    provider._indexes["old-snapshot"] = []
    provider._remember(merchant.latest)
    assert provider._records == {} and provider._indexes == {}
    provider.close()


def test_a_reply_with_no_product_list_raises_rather_than_returning_nothing() -> None:
    with pytest.raises(ProviderError, match="no product list"):
        ask(Merchant(store_result={"content": [{"type": "text", "text": "hello"}]}))


def test_a_store_with_zero_matches_returns_an_empty_list_not_an_error() -> None:
    assert ask(Merchant(store_result={"structuredContent": {"products": []}})).records == []


def test_inputs_the_store_does_not_take_are_refused_before_any_request() -> None:
    merchant = Merchant()
    for bad in ({"query": ""}, {"query": "x", "limit": 0}, {"query": "x", "colour": "red"}, {}):
        with (
            WebRouter("nimble", provider_config=router_config(merchant)) as router,
            pytest.raises(ProviderError, match="not valid"),
        ):
            router.live_data(DOMAIN, bad, provider="merchant")
    assert merchant.seen == []


def test_a_source_that_is_not_a_bare_host_is_not_a_store() -> None:
    merchant = Merchant()
    with pytest.raises(LiveDataSourceNotFoundError):
        ask(merchant, source="https://shop.example.com/../x")
    assert merchant.seen == []


def test_the_registry_lists_the_stores_it_has_tests_for_as_sources() -> None:
    merchant = Merchant()
    with WebRouter("nimble", provider_config=router_config(merchant)) as router:
        page = router.live_data_sources(provider="merchant")
    assert [s.name for s in page.sources] == [DOMAIN] and page.sources[0].provider == "merchant"
    assert page.total == 1


def test_a_described_source_advertises_an_option_only_where_the_registry_has_a_passing_test_for_it() -> None:
    merchant = Merchant()  # price_filter passes, localisation is "empty"
    with WebRouter("nimble", provider_config=router_config(merchant)) as router:
        source = router.live_data_source(DOMAIN, provider="merchant")
    properties = source.input_schema["properties"]  # type: ignore[index]
    assert {"query", "limit", "min_price", "max_price"} <= set(properties)
    assert "country" not in properties and "currency" not in properties
    assert source.input_schema["required"] == ["query"]  # type: ignore[index]


def test_the_backend_is_usable_without_being_named_once_the_profile_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_live_data_credentials(monkeypatch)
    merchant = Merchant()
    with WebRouter("nimble", provider_config={**router_config(merchant), "nimble": {}}) as router:
        reply = router.live_data(DOMAIN, {"query": "shoe"})
    assert reply.provider == "merchant"


async def test_the_async_router_makes_the_same_calls() -> None:
    merchant = Merchant()
    async with AsyncWebRouter("nimble", provider_config=router_config(merchant)) as router:
        reply = await router.live_data(DOMAIN, {"query": "shoe"}, provider="merchant")
        page = await router.live_data_sources(provider="merchant")
    assert reply.records == [PRODUCT] and [s.name for s in page.sources] == [DOMAIN]
    assert merchant.store_calls() == ["initialize", "notifications/initialized", "tools/call"]


def test_the_search_uses_the_version_the_store_answered_with_not_the_registrys_older_one() -> None:
    older = record()
    older["routes"][0]["protocol_version"] = "2025-06-18"
    merchant = Merchant(domain_record=older)
    reply = ask(merchant)
    assert merchant.headers[2]["mcp-protocol-version"] == "2025-06-18", "the handshake asks with the registry's version"
    assert merchant.headers[-1]["mcp-protocol-version"] == "2025-11-25"
    assert reply.extra["protocol_version"] == "2025-11-25"


def test_the_search_builder_puts_the_gated_arguments_last_so_an_option_cannot_replace_them() -> None:
    from web_router.providers.merchant_mcp import search
    from web_router.types_merchant import MerchantInputs

    call = search(MerchantInputs(query="shoe"), PROFILE, {"catalog": {"query": "other"}, "meta": {}})
    assert call["params"]["arguments"]["catalog"] == {"query": "shoe"}
    assert call["params"]["arguments"]["meta"] == {"ucp-agent": {"profile": PROFILE}}
