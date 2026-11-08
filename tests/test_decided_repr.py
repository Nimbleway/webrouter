"""What a decided call shows in its ``repr``: each URL a router or the prompt gave cut to its scheme and host."""

from __future__ import annotations

from web_router.routing.decide import Decided


def test_a_decided_calls_repr_shows_no_urls_path_or_query() -> None:
    secret = "https://user:pw@example.com/private/report?token=SECRET123"
    decided = Decided(tool="extract", providers=("jina",), params={"urls": [secret]})
    shown = repr(decided)
    assert "SECRET123" not in shown and "/private/report" not in shown and "pw@" not in shown
    assert "https://example.com" in shown and "'jina'" in shown
    assert decided.params["urls"] == [secret], "kept whole, to be called with"
    browse = Decided(tool="browse", providers=("x",), params={"task": "t", "start_url": secret})
    assert "SECRET123" not in repr(browse)
