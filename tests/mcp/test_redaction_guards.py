"""The devtools sweep is skipped when it cannot match, and runs when it can.

Every match of the devtools pattern pins the literal ``/devtools/``, so a page
that lacks it has nothing for the sweep to replace. These tests hold that line:
the sweep must not run without the marker, and must still run with it.
"""

from __future__ import annotations

import re
import types

import pytest

from web_router.mcp import redaction


def test_a_page_without_the_marker_skips_the_devtools_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    def swept(*_args: object, **_kwargs: object) -> tuple[str, int]:
        raise AssertionError("the devtools sweep ran on a page with no marker")

    monkeypatch.setattr(redaction, "_DEVTOOLS", types.SimpleNamespace(subn=swept, sub=swept))
    text = "a page about widgets " * 20
    assert redaction.redact(text) == (text, 0)


def test_the_marker_is_the_literal_every_devtools_match_carries() -> None:
    assert redaction._DEVTOOLS_MARKER in redaction._DEVTOOLS.pattern
    assert not redaction._DEVTOOLS.flags & re.IGNORECASE
    assert "(?i" not in redaction._DEVTOOLS.pattern
    for text in ("127.0.0.1:9222/devtools/page/AB", "/devtools/browser/x", "host/DevTools/x"):
        guarded = redaction.redact(text)
        swept = redaction._DEVTOOLS.subn(redaction.MCP_REDACTED, text)
        assert guarded == swept, text


def test_classify_answers_a_repeated_field_name_from_cache() -> None:
    redaction._classify_cached.cache_clear()
    assert redaction._classify("api_key") == redaction._classify("api_key")
    assert redaction._classify_cached.cache_info().hits == 1


def test_an_overlong_field_name_is_classified_but_not_kept() -> None:
    redaction._classify_cached.cache_clear()
    long_key = "live_view_url" + "a" * 5000
    before = redaction._classify_cached.cache_info()
    assert redaction._classify(long_key) == "session"
    assert redaction._classify(long_key) == "session"
    after = redaction._classify_cached.cache_info()
    assert (after.currsize, after.misses) == (before.currsize, before.misses), "an overlong key entered the memo"
