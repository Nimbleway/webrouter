"""One rule for which requested URL a backend's answer is, on the index built once.

The index groups the requested URLs by the form a backend may echo, so the work
is linear in the URLs asked for. These tests hold the two rules that keep the
grouping from guessing: an exact answer owns itself, and a rewrite two requested
URLs could both explain proves neither.
"""

from __future__ import annotations

import pytest

from web_router.providers._url_match import asked_for, owners


def test_an_exact_answer_owns_itself_even_when_a_bare_form_collides() -> None:
    urls = ["https://example.com/page", "https://example.com/page/"]
    assert owners(["https://example.com/page"], urls) == ["https://example.com/page"]


def test_a_rewrite_two_requested_urls_share_proves_neither() -> None:
    urls = ["https://example.com/page", "https://example.com/page/"]
    assert asked_for("https://example.com/page#top", urls) == set()
    assert owners(["https://example.com/page#top"], urls) == [None]


def test_a_url_no_parser_can_read_only_matters_once_a_rewrite_needs_it() -> None:
    with pytest.raises(ValueError):
        owners(["https://[::1/"], ["https://[::1"])
    assert owners(["https://[::1"], ["https://[::1"]) == ["https://[::1"]
