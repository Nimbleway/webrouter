"""The reference router server's own guards: loopback, its key, body limits, its worker limit, IPv6."""

from __future__ import annotations

import socket
import time

import pytest

from tests.routing_remote_helpers import KEY, reference, running, served_again
from web_router import WebRouter


def test_the_reference_server_binds_loopback_and_turns_away_a_foreign_host_no_key_or_a_large_body() -> None:
    import inspect

    import httpx

    assert inspect.signature(reference.serve).parameters["host"].default == "127.0.0.1"
    body = {"schema": 1, "prompt": "x", "candidates": [], "deadline_s": 1.0}
    with running() as server, httpx.Client(trust_env=False) as client:
        auth = {"Authorization": f"Bearer {KEY}"}
        assert client.post(server.url, json=body, headers=auth).status_code == 200
        assert client.post(server.url, json=body).status_code == 401
        assert client.post(server.url, json=body, headers={**auth, "Host": "rebound.example"}).status_code == 421
        big = b"{" + b" " * (1_000_001) + b"}"
        large = {**auth, "Content-Type": "application/json"}
        assert client.post(server.url, content=big, headers=large).status_code == 413


@pytest.mark.parametrize("length", ["9" * 5000, "\u00b2", "-1"], ids=["5000-digits", "superscript", "negative"])
def test_the_reference_server_answers_an_unreadable_content_length_with_413(length: str) -> None:
    with running() as server:
        host, port = server.server_address[:2]
        head = f"POST /route HTTP/1.1\r\nHost: {server.authority}\r\nAuthorization: Bearer {KEY}\r\n"
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(f"{head}Content-Length: {length}\r\n\r\n".encode())
            assert sock.recv(64).startswith(b"HTTP/1.0 413")


def test_the_reference_server_answers_a_too_deeply_nested_body_with_400() -> None:
    import httpx

    deep = b"[" * 200_000 + b"]" * 200_000  # under the 1 MB cap, deeper than json.loads recurses
    headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    with running() as server, httpx.Client(trust_env=False) as client:
        answer = client.post(server.url, content=deep, headers=headers)
        assert answer.status_code == 400 and "nested" in answer.json()["error"]
        body = {"schema": 1, "prompt": "x", "candidates": [], "deadline_s": 1.0}
        assert client.post(server.url, json=body, headers=headers).status_code == 200, "the server still answers"


def test_the_example_server_closes_connections_past_its_worker_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reference, "MAX_WORKERS", 2)
    with running() as server:
        address = server.server_address[:2]
        idle = [socket.create_connection(address) for _ in range(2)]  # hold both workers, sending nothing
        time.sleep(0.2)
        with socket.create_connection(address, timeout=2) as third:
            assert third.recv(1) == b"", "a third connection is closed, not given a thread"
        for conn in idle:
            conn.close()
        assert served_again(address), "the workers notice the close and give their slots back"


def test_the_example_server_serves_ipv6_loopback() -> None:
    server = reference.serve("::1", 0)
    try:
        assert server.url.startswith("http://[::1]:")
        plan = WebRouter(router="remote", router_config={"url": server.url}).plan("read https://example.com")
        assert plan.decision.reason != "router_error" and plan.tool == "extract"
    finally:
        server.shutdown()
        server.server_close()
