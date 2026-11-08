"""The merge rule and URLs: a router's URL is called with whole, and shown nowhere but as its scheme and host."""

from __future__ import annotations

import pytest

from tests.test_routing_merge import _merge
from web_router import DecisionRecord, RouteDecision

TOKEN_URL = "https://user:pw@a.example/page?session=s3cr3t-token#frag"


def test_a_url_is_kept_to_call_with_but_never_shown_whole() -> None:
    merged = _merge(RouteDecision(params={"browse": {"start_url": TOKEN_URL}}), pinned="browse", url_check=_none)
    assert merged.params == {"start_url": TOKEN_URL}, "the call gets the URL whole"
    assert merged.record()["params"] == {"start_url": "https://a.example"}
    extract = _merge(RouteDecision(params={"extract": {"urls": [TOKEN_URL]}}), pinned="extract", url_check=_none)
    assert extract.record()["params"] == {"urls": ["https://a.example"]}
    refused = _merge(RouteDecision(params={"extract": {"urls": [TOKEN_URL]}}), pinned="extract", url_check=_refuse)
    invalid = _merge(RouteDecision(params={"browse": {"start_url": [TOKEN_URL]}}), pinned="browse", url_check=_none)
    for shown in (repr(merged), repr(extract), repr(refused), repr(invalid), str(DecisionRecord(**merged.record()))):
        assert "s3cr3t" not in shown and "pw@" not in shown and "/page" not in shown
    assert "a.example" in refused.notes.ignored["params.extract.urls"]


def test_rule_7_a_url_the_caller_set_does_not_let_the_router_choose_extract() -> None:
    params = {"extract": {"urls": ["https://a.example"]}}
    merged = _merge(RouteDecision(tools=["extract"], params=params), url_check=_none, caller_set={"urls"})
    assert merged.tool == "search" and "extract" in merged.notes.ignored


def _none(url: str) -> None:
    return None


def _refuse(url: str) -> str:
    return "not allowed"


def test_a_url_in_the_routers_own_words_shows_as_its_scheme_and_host() -> None:
    decision = RouteDecision(
        providers={"search": ["exa"]},
        exclude={"search:brave": f"failed on {TOKEN_URL} twice"},
        reason=f"see {TOKEN_URL}",
    )
    merged = _merge(decision, pinned="search")
    empty = _merge(RouteDecision(reason=f"nothing for {TOKEN_URL}"), pinned="search")
    assert merged.notes.reason == "see https://a.example"
    assert merged.notes.excluded == {"search:brave": "failed on https://a.example twice"}
    for shown in (repr(merged), repr(empty), str(DecisionRecord(**merged.record())), str(empty.record())):
        assert "s3cr3t" not in shown and "pw@" not in shown


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("see https://a.example/page'session=secret now", "see https://a.example now"),
        ("see https://a.example'session=secret now", "see https://a.example now"),
        ("https://a.example/x(foo]session=secret", "https://a.example"),
        ("https://a.example/x)session=secret", "https://a.example"),
        ("https://a.example}session=secret", "https://a.example"),
        ("(see https://a.example/x?t=secret)", "(see https://a.example"),
        ("[https://a.example/a(b)c?t=secret]", "[https://a.example"),
        ("https://a.example/x>session=secret", "https://a.example"),
        (
            '"https://a.example/x?t=secret" and `https://b.example/y?t=secret`',
            '"https://a.example" and `https://b.example`',
        ),
        ("<https://a.example/x?t=secret>", "<https://a.example"),
        ("https://a.example/x?t=secret, then more", "https://a.example then more"),
    ],
)
def test_nothing_after_a_urls_host_survives_in_free_text(text: str, expected: str) -> None:
    from web_router.routing.merge_urls import hide_urls

    assert hide_urls(text) == expected and "secret" not in hide_urls(text)


def test_a_url_inside_an_id_or_a_note_the_router_wrote_is_hidden() -> None:
    decision = RouteDecision(
        providers={"search": [TOKEN_URL]},
        exclude={TOKEN_URL: "x"},
        params={"browse": {"start_url": TOKEN_URL}},
    )
    merged = _merge(decision, pinned="search")
    refused = _merge(
        RouteDecision(params={"browse": {"start_url": "https://a.example"}}),
        pinned="browse",
        url_check=lambda u: f"blocked, see {TOKEN_URL}",
    )
    for notes in (merged.notes.ignored, refused.notes.ignored):
        text = repr(notes)
        assert "s3cr3t" not in text and "pw@" not in text
    assert "search:https://a.example" in merged.notes.ignored


def test_a_refused_router_url_is_noted_when_the_call_searches_instead() -> None:
    params = {"extract": {"urls": ["http://10.0.0.1/x?t=s3cr3t"]}}
    merged = _merge(RouteDecision(tools=["extract"], params=params), url_check=lambda u: "a private address")
    assert merged.tool == "search" and "extract" in merged.notes.ignored
    note = merged.notes.ignored["params.extract.urls"]
    assert note == "a URL on http://10.0.0.1 is refused by the URL policy: a private address"
    unset = _merge(RouteDecision(tools=["extract"]))
    assert "params.extract.urls" not in unset.notes.ignored, "no router URL, nothing refused"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("see //a.example/page?session=secret now", "see //a.example now"),
        ("(//a.example/x'session=secret)", "(//a.example"),
        ("a//b and http://a.example/x?t=secret", "a//b and http://a.example"),
        ("x//a.example/?t=secret", "x//a.example"),
    ],
)
def test_a_scheme_relative_url_keeps_only_its_host(text: str, expected: str) -> None:
    from web_router.routing.merge_urls import hide_urls

    assert hide_urls(text) == expected
    merged = _merge(RouteDecision(providers={"search": ["exa"]}, reason=text), pinned="search")
    assert "secret" not in repr(merged) and "secret" not in str(merged.record())
