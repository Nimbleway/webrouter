"""The authority-ambiguity refusal: ``urlsplit`` and a browser must not read a different host.

In an http(s) authority a backslash ends the host for a WHATWG browser (it is a ``/``), an ``@``
brings userinfo and a ``%`` is decoded, so ``http://169.254.169.254\\@example.com/`` is the
metadata host to a browser but ``example.com`` to ``urlsplit``. Such a URL is refused before admission.
"""

from __future__ import annotations

import pytest

from web_router.mcp.navigation import NavigationPolicy

CDP = "ws://127.0.0.1:9333/devtools/browser/abc"

POLICY = NavigationPolicy.for_session(CDP, allow_files=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254\\@example.com/",
        "http://127.0.0.1:9333\\@example.com/",
        "http://169.254.169.254%2f@example.com/",
        "http://example.com@169.254.169.254/",
        "http://169.254.169.254/ ",
    ],
)
def test_an_ambiguous_authority_is_refused(url: str) -> None:
    assert POLICY.refusal(url) == "a browser could read the host differently from how it is checked"


def test_a_socket_may_not_open_to_an_ambiguous_authority() -> None:
    assert POLICY.refusal("wss://example.com\\@evil.test/", websocket=True) is not None


def test_an_unambiguous_host_stays_allowed() -> None:
    assert POLICY.refusal("https://example.com/path@2x") is None


def test_a_space_in_a_query_is_allowed_but_an_authority_trick_is_not() -> None:
    # A space in a query cannot move the host boundary, so the address reaches the browser;
    # a backslash or @ in the authority can, and is still refused before any navigation.
    assert POLICY.refusal("https://example.com/search?q=solar panels") is None
    for url in ("http://169.254.169.254\\@example.com/", "http://127.0.0.1:9333\\@example.com/"):
        assert POLICY.refusal(url) == "a browser could read the host differently from how it is checked"
