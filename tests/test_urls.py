"""The URL-in-prose finder, on its own."""

from __future__ import annotations

from web_router._urls import urls_in


def test_a_url_ending_a_sentence_loses_its_punctuation() -> None:
    assert urls_in("Read https://example.com/page. Then https://example.org/a?b=1, please!") == [
        "https://example.com/page",
        "https://example.org/a?b=1",
    ]
