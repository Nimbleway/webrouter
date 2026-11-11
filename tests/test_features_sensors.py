"""The ``features`` router's sensors: the opening move, a call for many items, a research word, a URL."""

from __future__ import annotations

import pytest

from web_router.routing.features.sensors import Verb, find_url, kw_research, plural, verb


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Who is the CEO of Example Corp?", Verb.QUESTION),
        ("In which year was the company founded", Verb.QUESTION),
        ("Extract the prices from https://example.com/a", Verb.EXTRACT),
        ("Please scrape this page", Verb.EXTRACT),
        ("Add two of them to my cart", Verb.ACT),
        ("Could you book a table for two", Verb.ACT),
        ("Find the cheapest flight to Lisbon", Verb.NAVIGATE),
        ("I'd like you to look up the opening hours", Verb.NAVIGATE),
        ("List every supplier in Spain", Verb.COMPILE),
        ("Give me a list of CRM tools", Verb.COMPILE),
        ("Analyze the impact of tariffs on steel", Verb.ANALYZE),
        ("Let's compare the two plans", Verb.ANALYZE),
        ("best laptops 2026", Verb.OTHER),
        ("https://example.com/pricing", Verb.OTHER),
    ],
)
def test_verb_classifies_the_opening_move(query: str, expected: Verb) -> None:
    assert verb(query) is expected


def test_verb_stays_fast_on_a_long_run_of_openers() -> None:
    assert verb("i want to " * 400 + "find it") is Verb.NAVIGATE


@pytest.mark.parametrize(
    "query",
    [
        "List all suppliers in Spain",
        "top 10 laptops",
        "the top five CRM tools",
        "every company that",
        "count the entries",
    ],
)
def test_plural_when_many_items_are_asked_for(query: str) -> None:
    assert plural(query)


@pytest.mark.parametrize(
    "query", ["Who is the CEO of Example Corp?", "Summarize https://example.com/a", "tallest building"]
)
def test_not_plural_for_one_item(query: str) -> None:
    assert not plural(query)


@pytest.mark.parametrize(
    "query",
    [
        "Write a report on EV adoption",
        "Why does node.js leak memory?",
        "How do tariffs affect steel prices",
        "pros and cons of remote work",
        "the market landscape for CRM",
    ],
)
def test_kw_research_when_a_research_word_appears(query: str) -> None:
    assert kw_research(query)


@pytest.mark.parametrize(
    "query", ["Who is the CEO of Example Corp?", "best laptops 2026", "Summarize https://example.com/a"]
)
def test_no_research_keyword(query: str) -> None:
    assert not kw_research(query)


@pytest.mark.parametrize(
    ("query", "url"),
    [
        ("Summarize https://example.com/pricing", "https://example.com/pricing"),
        ("http://example.com", "http://example.com"),
        ("HTTPS://Example.com/A?b=1#c", "HTTPS://Example.com/A?b=1#c"),
        ("What does https://example.com/pricing say?", "https://example.com/pricing"),
        ("Read https://example.com/a, then summarize.", "https://example.com/a"),
        ("(see https://example.com/docs)", "https://example.com/docs"),
        ("https://en.wikipedia.org/wiki/Python_(language)", "https://en.wikipedia.org/wiki/Python_(language)"),
        ("<https://example.com/x>", "https://example.com/x"),
        ("'https://example.com/q'", "https://example.com/q"),
        ("Check www.example.com/about today", "https://www.example.com/about"),
        ("Go to nfl.com and find the standings", "https://nfl.com"),
        ("Why does node.js leak memory?", "https://node.js"),
        ("Compare https://a.example.com and https://b.example.com", "https://a.example.com"),
        ("http://localhost:8080 then https://example.com", "https://example.com"),
        ("Read **https://example.com/a** now", "https://example.com/a"),
        ("| https://example.com/a|", "https://example.com/a"),
        ("\u201chttps://example.com/a\u201d", "https://example.com/a"),
        ("see https://example.com/a\u2026", "https://example.com/a"),
        ("\u8bf7\u770b https://example.com/a\u3002", "https://example.com/a"),
        ("[docs](https://example.com/a).", "https://example.com/a"),
    ],
    ids=[
        "url in a sentence",
        "http url alone",
        "upper-case scheme",
        "question mark after the url",
        "comma after the url",
        "url in parentheses",
        "parentheses inside the url",
        "url in angle brackets",
        "url in quotes",
        "www host without a scheme",
        "bare domain without a scheme",
        "dotted library name reads as a bare domain",
        "first of several urls",
        "private host skipped for the next url",
        "markdown bold around the url",
        "table pipe after the url",
        "curly quotes around the url",
        "ellipsis after the url",
        "cjk full stop after the url",
        "markdown link then a full stop",
    ],
)
def test_find_url_returns_the_first_fetchable_url(query: str, url: str) -> None:
    assert find_url(query) == url


@pytest.mark.parametrize(
    "query",
    [
        "Who is the CEO of Example Corp?",
        "email me at someone@www.example.com",
        "ftp://example.com/file",
        "https://",
        "https://.",
        "http://localhost:3000/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/",
    ],
    ids=[
        "no url",
        "www inside an email address",
        "non-http scheme",
        "scheme alone",
        "scheme and punctuation",
        "localhost",
        "cloud metadata address",
        "private address",
    ],
)
def test_find_url_finds_none(query: str) -> None:
    assert find_url(query) is None


def test_find_url_stays_linear_on_a_long_run_of_closing_brackets() -> None:
    assert find_url("https://example.com/a" + ")" * 9_978) == "https://example.com/a"
