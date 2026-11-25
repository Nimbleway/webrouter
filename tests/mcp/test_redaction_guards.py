"""The devtools sweep is skipped when it cannot match, and runs when it can.

Every match of the devtools pattern pins the literal ``/devtools/``, so a page
that lacks it has nothing for the sweep to replace. These tests hold that line:
the sweep must not run without the marker, and must still run with it.
"""

from __future__ import annotations

import types

import pytest

from web_router.mcp import redaction


def test_a_page_without_the_marker_skips_the_devtools_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    def swept(*_args: object, **_kwargs: object) -> tuple[str, int]:
        raise AssertionError("the devtools sweep ran on a page with no marker")

    monkeypatch.setattr(redaction, "_DEVTOOLS", types.SimpleNamespace(subn=swept, sub=swept))
    text = "a page about widgets " * 20
    assert redaction.redact(text) == (text, 0)


def test_a_marker_still_takes_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    # No scheme on purpose: a :// URL carrying the marker is redacted by the URL
    # rule first, so the marker is gone by the time the guard looks and the sweep
    # never runs. A bare path is the case the guard must let through.
    swept: list[str] = []
    real = redaction._DEVTOOLS

    class Recorder:
        sub = staticmethod(real.sub)

        @staticmethod
        def subn(repl: str, string: str) -> tuple[str, int]:
            swept.append(string)
            return real.subn(repl, string)

    monkeypatch.setattr(redaction, "_DEVTOOLS", Recorder)
    text = "the /devtools/json endpoint was probed"
    redacted, count = redaction.redact(text)
    assert swept == [text], "the sweep did not run on a value that has the marker"
    assert count == 1
    assert "/devtools/" not in redacted


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
