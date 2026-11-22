"""How ``auto``'s extract retry merges into the first answer: which pages it keeps, and in what order. Offline."""

from __future__ import annotations

from web_router.providers._auto_extract_chain import _merged
from web_router.types import ExtractedDocument, ExtractFailure, ExtractRequest, ExtractResponse

A, B, C, D = "https://a.test/p", "https://b.test/q", "https://c.test/r", "https://d.test/s"
A_LANDED, B_LANDED, C_LANDED = "https://www.a.test/p", "https://www.b.test/q", "https://www.c.test/r"
LOGIN = "https://www.linkedin.com/authwall?trk=redirect"
TEXT = "A page with enough text in it to count as the page that was asked for, not a shell."


def test_a_one_url_retry_keeps_its_own_page_and_drops_an_unrelated_one() -> None:
    first = ExtractResponse(provider="one", failures=[ExtractFailure(url=A, error="down")])
    second = ExtractResponse(
        provider="two",
        documents=[ExtractedDocument(url=A, content=TEXT), ExtractedDocument(url=C, content=TEXT)],
    )

    merged = _merged(first, second, [A], ExtractRequest(urls=[A]))

    assert [document.url for document in merged.documents] == [A]


def test_a_retried_page_echoed_with_a_slash_keeps_its_place() -> None:
    first = ExtractResponse(
        provider="one",
        documents=[ExtractedDocument(url=A, content=TEXT), ExtractedDocument(url=C, content=TEXT)],
        failures=[ExtractFailure(url=B, error="down")],
    )
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url=B + "/", content=TEXT)])

    merged = _merged(first, second, [A, B, C], ExtractRequest(urls=[A, B, C]))

    assert [document.url for document in merged.documents] == [A, B + "/", C]


def test_a_first_page_echoed_with_a_slash_keeps_its_place() -> None:
    first = ExtractResponse(
        provider="one",
        documents=[ExtractedDocument(url=A + "/", content=TEXT), ExtractedDocument(url=C, content=TEXT)],
        failures=[ExtractFailure(url=B, error="down")],
    )
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url=B, content=TEXT)])

    merged = _merged(first, second, [A, B, C], ExtractRequest(urls=[A, B, C]))

    assert [document.url for document in merged.documents] == [A + "/", B, C]


def test_first_pages_echoed_where_redirects_landed_keep_their_places() -> None:
    """No requested URL can be told for either page, so they take the two the first answer left, in turn."""
    first = ExtractResponse(
        provider="one",
        documents=[ExtractedDocument(url=A_LANDED, content=TEXT), ExtractedDocument(url=C_LANDED, content=TEXT)],
        failures=[ExtractFailure(url=B, error="down")],
    )
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url=B, content=TEXT)])

    merged = _merged(first, second, [A, B, C], ExtractRequest(urls=[A, B, C]))

    assert [document.url for document in merged.documents] == [A_LANDED, B, C_LANDED]


def test_a_first_page_no_requested_url_is_left_for_stays_last() -> None:
    first = ExtractResponse(
        provider="one",
        documents=[ExtractedDocument(url=A_LANDED, content=TEXT), ExtractedDocument(url=A, content=TEXT)],
        failures=[ExtractFailure(url=B, error="down")],
    )
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url=B, content=TEXT)])

    merged = _merged(first, second, [A, B], ExtractRequest(urls=[A, B]))

    assert [document.url for document in merged.documents] == [A, B, A_LANDED]


def test_a_first_page_echoed_as_another_url_asked_for_does_not_swap_the_first_pages() -> None:
    """The member read A and echoed B, read B and echoed where a redirect landed, and failed C. Giving the second
    page the one URL left, A, would put it ahead of the first, so each keeps only what its own URL tells."""
    first = ExtractResponse(
        provider="one",
        documents=[ExtractedDocument(url=B, content=TEXT), ExtractedDocument(url=B_LANDED, content=TEXT)],
        failures=[ExtractFailure(url=C, error="down")],
    )
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url=C, content=TEXT)])

    merged = _merged(first, second, [A, B, C], ExtractRequest(urls=[A, B, C]))

    assert [document.url for document in merged.documents] == [B, C, B_LANDED]


def test_first_pages_are_not_given_urls_in_turn_when_that_would_swap_them() -> None:
    first = ExtractResponse(
        provider="one",
        documents=[
            ExtractedDocument(url=B, content=TEXT),
            ExtractedDocument(url=B_LANDED, content=TEXT),
            ExtractedDocument(url=C_LANDED, content=TEXT),
        ],
        failures=[ExtractFailure(url=D, error="down")],
    )
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url=D, content=TEXT)])

    merged = _merged(first, second, [A, B, C, D], ExtractRequest(urls=[A, B, C, D]))

    assert [document.url for document in merged.documents] == [B, D, B_LANDED, C_LANDED]


def test_first_pages_told_by_their_own_urls_are_placed_whatever_order_they_came_in() -> None:
    """A member that takes every URL in one call answers in its own order; a page's own URL still tells its place."""
    first = ExtractResponse(
        provider="one",
        documents=[ExtractedDocument(url=C, content=TEXT), ExtractedDocument(url=A + "/", content=TEXT)],
        failures=[ExtractFailure(url=B, error="down")],
    )
    second = ExtractResponse(provider="two", documents=[ExtractedDocument(url=B, content=TEXT)])

    merged = _merged(first, second, [A, B, C], ExtractRequest(urls=[A, B, C]))

    assert [document.url for document in merged.documents] == [A + "/", B, C]


def test_a_retry_failure_under_a_url_never_sent_is_not_retried_again() -> None:
    first = ExtractResponse(provider="one", failures=[ExtractFailure(url=B, error="down")])
    second = ExtractResponse(
        provider="two",
        documents=[ExtractedDocument(url=B, content=TEXT)],
        failures=[ExtractFailure(url=LOGIN, error="blocked")],
    )

    merged = _merged(first, second, [A, B], ExtractRequest(urls=[A, B]))

    assert merged.failures == [], "the next member is not sent a URL the caller never asked for"
