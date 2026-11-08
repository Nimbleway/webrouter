"""The URL finder's edge cases, and ``url_policy``'s checks: the hosts each refuses, in every spelling."""

from __future__ import annotations

import pytest

from web_router._urls import urls_in
from web_router.constants_urls import MSG_URL_LOOPBACK, MSG_URL_METADATA
from web_router.exceptions import ConfigurationError
from web_router.routing.url_policy import any_web_url, public_only, screened, url_check

# -- the URL finder: http(s) URLs only -----------------------------------------


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("see https://a.test/x.", ["https://a.test/x"]),
        ("(https://a.test/x) and <https://b.test>", ["https://a.test/x", "https://b.test"]),
        (
            '"https://a.test/x?y=1&z=2", then http://b.test:8080/p!',
            ["https://a.test/x?y=1&z=2", "http://b.test:8080/p"],
        ),
        ("nfl.com scores", []),
        ("install node.js and run file.py", []),
        ("ftp://a.test/f and mailto:x@a.test", []),
        ("no url here", []),
    ],
    ids=["sentence-end", "brackets", "query-and-port", "bare-domain", "dotted-names", "other-schemes", "none"],
)
def test_the_finder_reads_only_http_urls(text: str, found: list[str]) -> None:
    assert urls_in(text) == found


# -- public_only ---------------------------------------------------------------

REFUSED = [
    "http://169.254.169.254/latest/meta-data",
    "http://metadata.google.internal/computeMetadata",
    "http://[fd00:ec2::254]/",
    "http://100.100.100.200/",
    "http://localhost:8080/",
    "http://app.localhost/",
    "http://127.0.0.1/",
    "http://0.0.0.0/",
    "http://[::1]/",
    "http://10.1.2.3/",
    "http://[fc00::1]/",  # ULA
    "http://2130706433/",  # decimal 127.0.0.1
    "http://LOCALHOST./",
    "http://127.0x.0x.1/",  # a bare 0x octet is 0: 127.0.0.1
    "http://0x/",  # 0.0.0.0
    "http://10.0x.0x.1/",
    "http://192.168.0x.1/",
]


@pytest.mark.parametrize("url", REFUSED)
def test_public_only_refuses_private_loopback_link_local_and_metadata_hosts(url: str) -> None:
    assert public_only(url) is not None
    assert any_web_url(url) is None, "with no policy, an http(s) URL is never refused"


@pytest.mark.parametrize(
    "url", ["https://example.com/a", "http://8.8.8.8/", "https://[2606:4700::1111]/", "http://[::ffff:8.8.8.8]/"]
)
def test_public_only_admits_public_hosts(url: str) -> None:
    assert public_only(url) is None


AMBIGUOUS = [
    "http://127.0.0.1\\.public.example/",  # a browser ends the host at the backslash: loopback
    "http://user@public.example/",  # userinfo
    "http://%31%32%37.0.0.1/",  # a browser decodes a percent-encoded host
    "http://public.ex\tample/",  # a browser strips a tab
    "http://public.example\x00/",
    "http://public.exa\x7fmple/",  # DEL, the one control character above 0x1F
]


@pytest.mark.parametrize("url", AMBIGUOUS)
def test_public_only_refuses_a_url_a_browser_could_read_another_host_from(url: str) -> None:
    from web_router.constants_urls import MSG_URL_AMBIGUOUS

    assert public_only(url) == MSG_URL_AMBIGUOUS


@pytest.mark.parametrize(
    "url",
    [
        "http://\uff11\uff12\uff17.\uff10.\uff10.\uff11/",  # full-width digits
    ],
)
def test_public_only_judges_a_non_ascii_host_as_the_ascii_a_browser_maps_it_to(url: str) -> None:
    assert public_only(url) in {MSG_URL_LOOPBACK, MSG_URL_METADATA}


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://127.0.0.1\u3002/", MSG_URL_LOOPBACK),  # an ideographic full stop that IDNA makes a trailing dot
        ("http://localhost\u3002/", MSG_URL_LOOPBACK),
    ],
)
def test_one_trailing_dot_is_dropped_before_the_host_is_judged(url: str, why: str) -> None:
    assert public_only(url) == why


def test_a_backslash_or_userinfo_in_the_path_or_query_is_no_host_confusion() -> None:
    assert public_only("https://example.com/a\\b?next=x@y.test") is None


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "https://", "http://[::1"])
def test_every_policy_refuses_what_is_no_http_url(url: str) -> None:
    assert public_only(url) is not None and any_web_url(url) is not None


@pytest.mark.parametrize("url", ["http:/example.com/", "https://"])
def test_an_http_url_with_no_host_is_refused_for_its_missing_host_not_its_scheme(url: str) -> None:
    from web_router.constants_urls import MSG_URL_NO_HOST

    assert public_only(url) == MSG_URL_NO_HOST and any_web_url(url) == MSG_URL_NO_HOST


def test_a_refused_url_is_recorded_by_its_host_only() -> None:
    kept, refused = screened(["https://a.test/x", "http://10.0.0.1/secret?token=1"], public_only)
    assert kept == ["https://a.test/x"]
    assert list(refused) == ["url:http://10.0.0.1"]


def test_an_unknown_policy_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="url_policy"):
        url_check("private_ok")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "url", ["http://[::ffff:169.254.169.254]/", "http://0251.0376.0251.0376/", "http://2852039166/"]
)
def test_a_metadata_address_in_any_spelling_is_named_as_one(url: str) -> None:
    from web_router.constants_urls import MSG_URL_METADATA

    assert public_only(url) == MSG_URL_METADATA


@pytest.mark.parametrize("url", ["http://010.8.8.8/"])
def test_public_only_refuses_a_numeric_spelling_resolvers_read_differently(url: str) -> None:
    # A browser reads 010 as octal (8), macOS's getaddrinfo as decimal (10): no public URL needs the spelling.
    from web_router.constants_urls import MSG_URL_AMBIGUOUS

    assert public_only(url) == MSG_URL_AMBIGUOUS


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://[64:ff9b::a9fe:a9fe]/", MSG_URL_METADATA),  # NAT64 169.254.169.254
        ("http://[64:ff9b::127.0.0.1]/", MSG_URL_LOOPBACK),
        ("http://[::127.0.0.1]/", MSG_URL_LOOPBACK),  # IPv4-compatible
        ("http://[::7f00:1]/", MSG_URL_LOOPBACK),
    ],
)
def test_an_ipv4_address_carried_in_ipv6_is_judged_as_that_ipv4(url: str, why: str) -> None:
    assert public_only(url) == why


def test_the_ipv6_forms_of_a_public_ipv4_and_ipv6_loopback_are_read_right() -> None:
    from web_router._hosts import host_address, is_loopback

    assert public_only("http://[64:ff9b::808:808]/") is None
    assert str(host_address("64:ff9b::808:808")) == "8.8.8.8"
    assert is_loopback("::1") and str(host_address("::1")) == "::1", "::1 is IPv6's own loopback, not 0.0.0.1"


def test_the_browser_navigation_guard_reads_a_bare_0x_octet_as_zero_too() -> None:
    from web_router.mcp.navigation import NavigationPolicy

    policy = NavigationPolicy.for_session("ws://127.0.0.1:9333/devtools/browser/abc", allow_files=False)
    reason = policy.refusal("http://127.0x.0x.1:9333/json")
    assert reason is not None and "debugging endpoint" in reason
