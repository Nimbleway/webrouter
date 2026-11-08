"""The URL-in-prose finder, on its own."""

from __future__ import annotations

import pytest

from web_router._urls import host_of, urls_in


def test_a_url_ending_a_sentence_loses_its_punctuation() -> None:
    assert urls_in("Read https://example.com/page. Then https://example.org/a?b=1, please!") == [
        "https://example.com/page",
        "https://example.org/a?b=1",
    ]


def test_a_closing_bracket_the_url_opened_is_kept_and_one_it_did_not_is_dropped() -> None:
    wiki = "https://en.wikipedia.org/wiki/Python_(programming_language)"
    assert urls_in(f"see {wiki}.") == [wiki]
    assert urls_in(f"(see {wiki})") == [wiki]
    assert urls_in("open http://[::1]/admin, or (http://[::1]:8080/)") == ["http://[::1]/admin", "http://[::1]:8080/"]


def test_a_url_named_twice_is_found_twice_and_plan_takes_it_once() -> None:
    assert urls_in("https://a.test/x and https://a.test/x") == ["https://a.test/x", "https://a.test/x"]


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("http://[64:ff9b::7f00:1]/admin?t=s", "http://[64:ff9b::7f00:1]"),
        ("https://[::1]:8443/x", "https://[::1]"),
        ("https://user:pw@example.com/p?token=s", "https://example.com"),
    ],
)
def test_a_url_shows_only_its_scheme_and_host_an_ipv6_host_in_its_brackets(url: str, shown: str) -> None:
    assert host_of(url) == shown


def test_a_shown_ipv6_host_keeps_its_brackets_and_drops_its_scope_id() -> None:
    from web_router._urls import host_of

    assert host_of("http://[fe80::1%25eth0]/x?token=1") == "http://[fe80::1]"
    assert host_of("http://[fe80::1%eth0]/") == "http://[fe80::1]"
    assert host_of("https://[2001:db8::1]:8443/p") == "https://[2001:db8::1]"
