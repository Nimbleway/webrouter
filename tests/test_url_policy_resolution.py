"""``public_only``: a name is judged by the one set of addresses it resolves to."""

from __future__ import annotations

import socket

import pytest

from web_router.constants_urls import MSG_URL_LOOPBACK, MSG_URL_METADATA, MSG_URL_PRIVATE
from web_router.routing.url_policy import public_only


def _lookups(monkeypatch: pytest.MonkeyPatch, table: dict[str, list[str]]) -> None:
    """``socket.getaddrinfo`` answering from ``table``, a public IPv4 for any other name."""

    def fake(host: str, port: object, *args: object, **kwargs: object) -> list[tuple[object, ...]]:
        addresses = table.get(host, ["93.184.215.14"])
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 80))
            for address in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.mark.parametrize(
    ("host", "addresses", "why"),
    [
        ("rebind.example", ["127.0.0.1"], MSG_URL_LOOPBACK),  # a public name whose A record points at loopback
        ("metadata.example", ["169.254.169.254"], MSG_URL_METADATA),
        ("intranet.example", ["10.1.2.3"], MSG_URL_PRIVATE),
        ("ula.example", ["fc00::1"], MSG_URL_PRIVATE),  # an IPv6 answer is judged too
        ("alias.example", ["93.184.215.14", "192.168.1.1"], MSG_URL_PRIVATE),  # any answer refuses, not only the first
    ],
)
def test_public_only_refuses_a_name_that_resolves_to_a_non_public_address(
    monkeypatch: pytest.MonkeyPatch, host: str, addresses: list[str], why: str
) -> None:
    _lookups(monkeypatch, {host: addresses})
    assert public_only(f"http://{host}:8080/x") == why


def test_public_only_admits_a_name_that_resolves_only_to_public_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    _lookups(monkeypatch, {"public.example": ["93.184.215.14", "2606:4700::1111"]})
    assert public_only("https://public.example/x") is None


def test_public_only_admits_a_name_whose_answer_changes_between_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    # A public name may answer differently on the next lookup; every answer here is public.
    answers = iter([["93.184.215.14"], ["93.184.215.15"]])

    def fake(host: str, port: object, *args: object, **kwargs: object) -> list[tuple[object, ...]]:
        address = next(answers)[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 80))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert public_only("http://rebind.example/x") is None


def test_public_only_judges_a_literal_address_without_a_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("a literal address was looked up")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert public_only("http://8.8.8.8/") is None
