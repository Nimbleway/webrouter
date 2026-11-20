"""The URL-in-prose finder, on its own."""

from __future__ import annotations

import time

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


@pytest.mark.parametrize("scheme", ["HTTP", "HTTPS", "Http", "hTtPs"])
def test_a_urls_scheme_is_case_insensitive_and_its_path_is_preserved(scheme: str) -> None:
    url = f"{scheme}://example.com/Report?Token=AbC"
    assert urls_in(f"Read ({url}).") == [url]


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


def test_a_non_ascii_host_is_shown_whole_as_the_ascii_a_browser_maps_it_to() -> None:
    assert host_of("https://münchen.de/x?token=1") == "https://xn--mnchen-3ya.de"
    assert host_of("https://köln.de/") != host_of("https://münchen.de/"), "two refused hosts keep two keys"


@pytest.mark.parametrize(
    "text",
    [
        "Summarize “https://example.com/report”",
        "Summarize \u2018https://example.com/report\u2019",
        "Summarize «https://example.com/report»",
        "请总结 https://example.com/report\uff0c谢谢",
        "見て https://example.com/report。",
        "参考「https://example.com/report」",
        "Summarize **https://example.com/report**",
    ],
    ids=["curly-double", "curly-single", "guillemets", "cjk-comma", "cjk-full-stop", "cjk-brackets", "md-bold"],
)
def test_typographic_quotes_cjk_punctuation_and_emphasis_end_a_url(text: str) -> None:
    assert urls_in(text) == ["https://example.com/report"]


def test_a_non_ascii_path_keeps_its_letters_and_its_dash() -> None:
    for url in ("https://de.wikipedia.org/wiki/München", "https://en.wikipedia.org/wiki/Mason\u2013Dixon_line"):
        assert urls_in(f"see {url}.") == [url]


def test_a_markdown_link_whose_text_is_a_url_yields_its_target_too() -> None:
    text = "see [https://example.com](https://example.com/pricing) and https://other.com/x"
    assert urls_in(text) == ["https://example.com", "https://example.com/pricing", "https://other.com/x"]


def test_a_long_run_of_cut_url_fragments_is_read_in_one_pass() -> None:
    # Each `]` cuts a URL that the run goes on past; rescanning that run from every cut was quadratic.
    fragments = 20_000
    start = time.perf_counter()
    found = urls_in("https://a]" * fragments)
    assert len(found) == fragments
    assert time.perf_counter() - start < 2.0, "about 0.1 s in one pass; well over 30 s when rescanned"


def test_a_url_in_another_urls_query_stays_part_of_it() -> None:
    assert urls_in("go to https://a.test/?next=https://b.test/x now") == ["https://a.test/?next=https://b.test/x"]
