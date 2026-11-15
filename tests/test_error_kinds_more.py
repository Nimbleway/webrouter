"""Failure kinds read from a bare status, a shared kind across URLs, the retry delay of ``auto``, and HTML shells."""

from __future__ import annotations

import pytest

from web_router import AllProvidersFailedError
from web_router.exceptions import ProviderError
from web_router.providers._error_pages import near_empty
from web_router.types import ExtractedDocument, ExtractFailure


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_page_status_alone_is_no_refused_key(status: int) -> None:
    """On a failed page the status is the site's: a site's own 403 says nothing about the backend's key."""
    assert ExtractFailure(url="https://a.test", error="failed", status_code=status).kind not in ("auth", "rate_limit")


def test_the_backends_own_refusal_keeps_its_kind() -> None:
    refused = ProviderError("nimble", "refused", kind="auth")
    assert ExtractFailure.from_error("https://a.test", refused).kind == "auth"


def test_urls_that_agree_on_a_kind_share_it() -> None:
    failures = [ExtractFailure(url=f"https://{n}.test", error="x", kind="rate_limit") for n in "ab"]
    assert ExtractFailure.shared_kind(failures) == "rate_limit"
    assert ExtractFailure.shared_kind([*failures, ExtractFailure(url="c", error="x", status_code=404)]) is None


def test_auto_keeps_the_longest_member_delay() -> None:
    members = {"a": ProviderError("a", "slow", kind="rate_limit"), "b": ProviderError("b", "slow", kind="rate_limit")}
    members["a"].retry_after, members["b"].retry_after = 2.0, 7.0
    assert AllProvidersFailedError("auto", "all failed", members).retry_after == 7.0


def test_a_script_shell_is_near_empty() -> None:
    shell = "<html><head><script>" + "x=1;" * 2000 + "</script></head><body><div>Loading</div></body></html>"
    assert near_empty(ExtractedDocument(url="https://a.test", content=shell, format="html"))


def test_a_longer_name_sharing_a_login_path_start_is_a_page() -> None:
    from web_router.providers._error_pages import not_the_page

    page = ExtractedDocument(url="https://example.com/authwallpaper", content="x" * 5000)
    assert not_the_page(page) is None
    assert not_the_page(page.model_copy(update={"url": "https://example.com/authwall/x"})) is not None


def test_a_retry_that_reads_a_canonical_url_clears_the_failure_and_a_later_kind_wins() -> None:
    from web_router.providers._auto_extract_chain import _merged as merged
    from web_router.types import ExtractResponse

    first = ExtractResponse(
        provider="a", documents=[], failures=[ExtractFailure(url="https://a.test/p", error="down", kind="upstream")]
    )
    read = ExtractResponse(provider="b", documents=[ExtractedDocument(url="https://A.test/p/", content="hi")])
    assert merged(first, read, ["https://a.test/p"]).failures == []
    gone = ExtractResponse(
        provider="b", documents=[], failures=[ExtractFailure(url="https://a.test/p", error="gone", status_code=404)]
    )
    assert merged(first, gone, ["https://a.test/p"]).failures[0].kind == "target"
