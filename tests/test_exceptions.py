"""describe(): what an error message says about an exception."""

from __future__ import annotations

import httpx

from web_router.exceptions import describe


def test_an_exception_with_text_is_described_by_its_text() -> None:
    assert describe(ValueError("bad input")) == "bad input"


def test_an_exception_with_no_text_is_named_by_its_type() -> None:
    assert describe(httpx.ReadTimeout("")) == "ReadTimeout"
    assert describe(RuntimeError()) == "RuntimeError"
