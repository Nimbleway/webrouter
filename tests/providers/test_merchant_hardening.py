"""``merchant`` against malformed registry records, odd inputs and shared use: what must refuse or hold, not crash."""

from __future__ import annotations

import pytest

from tests._merchant_fake import DOMAIN, Merchant, record, router_config
from web_router import ProviderError, WebRouter
from web_router.exceptions_merchant import MerchantNotValidatedError, MerchantRouteError


def ask(merchant: Merchant, query: str = "shoe", **inputs: object):  # type: ignore[no-untyped-def]
    with WebRouter("nimble", provider_config=router_config(merchant)) as router:
        return router.live_data(DOMAIN, {"query": query, **inputs}, provider="merchant")


def test_a_tests_field_that_is_not_a_mapping_is_a_refusal_not_a_crash() -> None:
    bad = record()
    bad["commerce_evidence"]["tests"] = ["search_keyword"]
    merchant = Merchant(domain_record=bad)
    with pytest.raises(MerchantRouteError):
        ask(merchant)
    assert merchant.store_calls() == []


@pytest.mark.parametrize("field", ["limit", "min_price", "max_price"])
def test_a_boolean_is_not_a_number_for_the_numeric_inputs(field: str) -> None:
    merchant = Merchant(domain_record=record(tests={"price_filter": "pass"}))
    with pytest.raises(ProviderError, match="boolean"):
        ask(merchant, **{field: True})
    assert merchant.store_calls() == []


def test_the_session_id_is_sent_back_under_the_one_header_name_the_constant_gives() -> None:
    merchant = Merchant()
    ask(merchant)
    assert merchant.headers[-1]["mcp-session-id"] == "s-1"


@pytest.mark.parametrize("newline", ["\r", "\r\n", "\n"])
def test_an_event_stream_may_end_its_lines_with_cr_lf_or_crlf(newline: str) -> None:
    from web_router.providers.merchant_mcp import _last_event_data

    stream = newline.join(["event: message", 'data: {"a": 1}', "", ""])
    assert _last_event_data(stream) == '{"a": 1}'


def test_a_product_list_with_nothing_readable_is_an_unreadable_reply_not_no_matches() -> None:
    merchant = Merchant(store_result={"structuredContent": {"products": ["x", 3, None]}})
    with pytest.raises(ProviderError, match="product list"):
        ask(merchant)


def test_one_malformed_index_entry_does_not_break_the_store_listing() -> None:
    merchant = Merchant()
    merchant.index["sites"] += [
        {"domain": None, "commerce_tests": {"x": "pass"}},
        {"domain": 7, "commerce_tests": {"x": 1}},
    ]
    with WebRouter("nimble", provider_config=router_config(merchant)) as router:
        page = router.live_data_sources(provider="merchant")
    assert [s.name for s in page.sources] == [DOMAIN]


def test_the_merchant_errors_import_from_the_package() -> None:
    import web_router

    assert web_router.MerchantRouteError is MerchantRouteError
    assert web_router.MerchantNotValidatedError is MerchantNotValidatedError


def test_threads_sharing_one_router_can_fill_and_rebuild_the_caches_together(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    monkeypatch.setattr("web_router.providers.merchant_registry.REGISTRY_POINTER_TTL_S", 0)
    merchant = Merchant()
    failures: list[BaseException] = []

    def work(router: WebRouter) -> None:
        try:
            for _ in range(25):
                router.live_data(DOMAIN, {"query": "shoe"}, provider="merchant")
        except BaseException as exc:
            failures.append(exc)

    with WebRouter("nimble", provider_config=router_config(merchant)) as router:
        threads = [threading.Thread(target=work, args=(router,)) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    assert failures == []
