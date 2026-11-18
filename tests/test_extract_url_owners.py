"""Which requested URL a page or failure answers, in a batch and on ``auto``'s retry, and what a retry costs."""

from __future__ import annotations

from typing import Any

import pytest

from web_router.providers import _auto_extract_chain as chain
from web_router.providers._auto_extract_chain import _merged
from web_router.providers._error_pages import named_failures, split_error_pages
from web_router.providers._url_match import owners
from web_router.routing.metered import combined_cost, metered
from web_router.types import ExtractedDocument, ExtractFailure, ExtractRequest, ExtractResponse

A, B, C = "https://a.test/p", "https://b.test/q", "https://c.test/r"
LOGIN = "https://www.linkedin.com/authwall?trk=redirect"
TEXT = "A page with enough text in it to count as the page that was asked for, not a shell."


def test_a_batch_names_a_redirected_login_wall_by_the_one_url_left_unanswered() -> None:
    pages = [ExtractedDocument(url=A, content=TEXT), ExtractedDocument(url=LOGIN, content="Sign in")]

    kept, failures = split_error_pages(pages, [A, B])

    assert [page.url for page in kept] == [A]
    assert [failure.url for failure in failures] == [B]


def test_a_batch_cannot_name_a_redirect_two_urls_could_have_made() -> None:
    assert owners([LOGIN], [A, B]) == [None]
    assert owners([LOGIN], [A, B], also_claimed=[A]) == [B]  # the backend reported A failed itself
    assert owners([LOGIN, "https://elsewhere.test/"], [A, B, C], also_claimed=[A]) == [None, None]


def test_a_failure_the_backend_reported_after_a_redirect_is_named_by_the_url_asked_for() -> None:
    reported = ExtractFailure(url=LOGIN, error="blocked", kind="target", status_code=404)
    response = ExtractResponse(provider="p", documents=[ExtractedDocument(url=A, content=TEXT)], failures=[reported])

    [failure] = named_failures(response, [A, B]).failures

    assert (failure.url, failure.error, failure.status_code) == (B, "blocked", 404)
    assert failure.kind == "upstream", "a 404 where a redirect landed says nothing certain about the URL asked for"


def test_a_reported_failure_keeps_its_own_url_when_two_urls_could_own_it() -> None:
    response = ExtractResponse(provider="p", failures=[ExtractFailure(url=LOGIN, error="blocked")])

    assert named_failures(response, [A, B]).failures[0].url == LOGIN
    assert named_failures(response, [A]).failures[0].url == A, "a one-URL call's failure is that URL's"


def test_a_retry_leaves_out_a_page_no_url_it_was_sent_for_can_be_told() -> None:
    first = ExtractResponse(
        provider="one", failures=[ExtractFailure(url=A, error="down"), ExtractFailure(url=B, error="down")]
    )
    # Two URLs went out and one page came back under a third address: it could be either one's.
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url="https://x.test/", content=TEXT)])

    merged = _merged(first, second, [A, B], ExtractRequest(urls=[A, B]))

    assert merged.documents == []
    assert [failure.url for failure in merged.failures] == [A, B]


def test_a_single_url_retry_counts_a_redirected_page_as_read() -> None:
    first = ExtractResponse(provider="one", failures=[ExtractFailure(url=A, error="down")])
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url=A + "/landed", content=TEXT)])

    merged = _merged(first, second, [A], ExtractRequest(urls=[A]))

    assert merged.failures == []
    assert len(merged.documents) == 1


def test_a_retry_adds_the_second_backends_cost() -> None:
    first = ExtractResponse(
        provider="one",
        documents=[ExtractedDocument(url=A, content=TEXT)],
        failures=[ExtractFailure(url=B, error="down")],
        cost_usd=0.002,
        cost_source="reported",
    )
    second = ExtractResponse(
        provider="two", documents=[ExtractedDocument(url=B, content=TEXT)], cost_usd=0.001, cost_source="reported"
    )

    merged = _merged(first, second, [A, B], ExtractRequest(urls=[A, B]))

    assert merged.cost_usd == 0.003
    assert merged.cost_source == "reported"


def test_a_combined_cost_is_estimated_unless_every_part_reported_and_unknown_if_any_part_is() -> None:
    request = ExtractRequest(urls=[A])
    reported = ExtractResponse(provider="one", cost_usd=0.002, cost_source="reported")
    estimated = ExtractResponse(provider="two", cost_usd=0.001, cost_source="estimated")

    assert combined_cost("extract", [(reported, request), (estimated, request)])["cost_source"] == "estimated"
    unknown = combined_cost("extract", [(reported, request), (ExtractResponse(provider="unpriced"), request)])
    assert unknown == {"cost_usd": None, "cost_source": None}


def test_a_merged_answer_with_an_unpriced_retry_is_not_estimated_as_the_first_backends_alone() -> None:
    first = ExtractResponse(provider="one", failures=[ExtractFailure(url=A, error="down")], cost_usd=0.002)
    second = ExtractResponse(provider="unpriced", documents=[ExtractedDocument(url=A, content=TEXT)])

    merged = metered(_merged(first, second, [A], ExtractRequest(urls=[A])), "extract", ExtractRequest(urls=[A]), 0.0)

    assert merged.cost_usd is None and merged.cost_source is None


def test_a_retry_costs_only_the_urls_it_was_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    first = ExtractResponse(
        provider="one",
        documents=[],
        failures=[ExtractFailure(url=A, error="gone", kind="target"), ExtractFailure(url=B, error="down")],
        cost_usd=0.002,
        cost_source="reported",
    )
    second = ExtractResponse(
        provider="two", documents=[ExtractedDocument(url=B, content=TEXT)], cost_usd=0.001, cost_source="reported"
    )
    sent: list[list[str]] = []

    def recorded(tool: Any, parts: Any) -> dict[str, Any]:
        sent.append(list(parts[1][1].urls))
        return combined_cost(tool, parts)

    monkeypatch.setattr(chain, "combined_cost", recorded)
    merged = _merged(first, second, [A, B], ExtractRequest(urls=[A, B]))

    assert sent == [[B]], "the 404 on A never went on the retry, so the retry is not charged for it"
    assert [failure.url for failure in merged.failures] == [A]


def test_a_retried_page_redirected_onto_a_later_url_still_comes_back_in_request_order() -> None:
    first = ExtractResponse(
        provider="one",
        documents=[ExtractedDocument(url=B, content="B's own page, read the first time round.")],
        failures=[ExtractFailure(url=A, error="down")],
    )
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url=B, content=TEXT)])

    merged = _merged(first, second, [A, B], ExtractRequest(urls=[A, B]))

    assert [document.content for document in merged.documents] == [TEXT, "B's own page, read the first time round."]


def test_a_cost_left_unknown_by_one_merge_stays_unknown_through_the_next() -> None:
    request = ExtractRequest(urls=[A])
    unknown = ExtractResponse(provider="one", cost_usd=0.002).model_copy(update={"cost_usd": None})
    priced = ExtractResponse(provider="three", cost_usd=0.001, cost_source="reported")

    assert combined_cost("extract", [(unknown, request), (priced, request)])["cost_usd"] is None


def test_two_urls_redirected_to_two_walls_fail_under_the_urls_asked_for() -> None:
    walls = [ExtractedDocument(url=LOGIN, content="Sign in"), ExtractedDocument(url=LOGIN + "&b", content="Sign in")]

    kept, failures = split_error_pages([*walls, ExtractedDocument(url=C, content=TEXT)], [A, B, C])

    assert [page.url for page in kept] == [C]
    assert sorted(failure.url for failure in failures) == [A, B], "auto retries A and B, not the walls"
