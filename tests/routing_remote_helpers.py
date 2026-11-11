"""What the remote router's tests share: the reference server from examples/ on a free loopback port, and builders."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests.examples.helpers import load_example
from web_router import RouteDecision, RouteRequest
from web_router.routing.remote import RemoteRouter
from web_router.routing.wire import request_from_wire

reference = load_example("router_remote_server")

KEY = "router-service-key-0123456789"
"""The router service's own bearer key: the one credential a request may carry."""


class Recorder:
    """A ``decide`` that keeps every body it was given and answers with ``answer``, or the reference server's own."""

    def __init__(self, answer: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.raw: list[str] = []
        self.answer = answer or reference.decide

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        self.raw.append(json.dumps(body))
        return self.answer(body)


@contextmanager
def running(
    decide: Callable[[dict[str, Any]], dict[str, Any]] | None = None, *, api_key: str | None = KEY
) -> Iterator[Any]:
    """The reference server on 127.0.0.1, a free port, answering with ``decide``; shut down on exit."""
    server = reference.serve("127.0.0.1", 0, api_key=api_key)
    if decide is not None:
        server.decide = decide
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def config(server: Any, **extra: Any) -> dict[str, Any]:
    """``router_config`` for the ``remote`` router pointed at ``server``, with its key."""
    return {"url": server.url, "api_key": KEY, **extra}


class Canned:
    """What a hand-written server answers, and every request it was sent: path, headers.

    ``protocol`` ``HTTP/1.1`` keeps each connection open after its answer, as most services do.
    """

    def __init__(
        self,
        status: int = 200,
        body: bytes = b"{}",
        headers: dict[str, str] | None = None,
        drip_s: float = 0.0,
        protocol: str = "HTTP/1.0",
    ) -> None:
        self.status, self.body, self.drip_s, self.protocol = status, body, drip_s, protocol
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.seen: list[tuple[str, dict[str, str]]] = []


@contextmanager
def canned(answer: Canned) -> Iterator[str]:
    """A server on a free loopback port that answers every request with ``answer``; yields its base URL."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = answer.protocol

        def _any(self) -> None:
            answer.seen.append((self.path, dict(self.headers)))
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(answer.status)
            for name, value in answer.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(answer.body)))
            self.end_headers()
            try:
                for byte in answer.body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(answer.drip_s)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client stopped reading, as one refusing an oversized or late answer does

        do_POST = do_GET = _any

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


URL_PROMPT = "read https://en.wikipedia.org/wiki/Web_scraping"

SLOW_CLOSE_S = 5.0
"""How long a stuck pool takes to close: far past any wait a test allows."""

SHORT_WAIT_S = 0.05
"""The close wait a test sets, so a stuck close is given up on at once."""


def sample_request() -> RouteRequest:
    """A schema-1 request with one search candidate."""
    return request_from_wire(
        {"schema": 1, "prompt": "solar news", "candidates": [{"tool": "search", "provider": "exa"}], "deadline_s": 1.0}
    )


def ask(router: RemoteRouter) -> RouteDecision:
    """``router``'s answer to :func:`sample_request`, on a loop of its own."""
    return asyncio.run(router.aroute(sample_request()))


@contextmanager
def loop_thread() -> Iterator[asyncio.AbstractEventLoop]:
    """An event loop running on a thread of its own, as web-router's private loop does."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()


def on_loop(loop: asyncio.AbstractEventLoop, router: RemoteRouter) -> RouteDecision:
    """``router``'s answer to :func:`sample_request`, asked on ``loop`` from this thread."""
    return asyncio.run_coroutine_threadsafe(router.aroute(sample_request()), loop).result(5)


def stuck(pool: Any, delay_s: float = SLOW_CLOSE_S) -> list[str]:
    """Make ``pool`` take ``delay_s`` to close; what it did is recorded."""
    done: list[str] = []
    real = pool.aclose

    async def slow() -> None:
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            done.append("cancelled")
            raise
        await real()
        done.append("closed")

    pool.aclose = slow
    return done


def _answers(address: tuple[str, int]) -> bool:
    """Whether a new connection to the reference server at ``address`` is served: its 404 for an unknown path."""
    with socket.create_connection(address, timeout=2) as conn:
        conn.sendall(b"POST /nowhere HTTP/1.1\r\nHost: " + f"{address[0]}:{address[1]}".encode() + b"\r\n\r\n")
        return conn.recv(12).startswith(b"HTTP/1.0 404")


def served_again(address: tuple[str, int], wait_s: float = 2.0) -> bool:
    """Whether the reference server serves a new connection within ``wait_s``, once its workers are free."""
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if _answers(address):
            return True
        time.sleep(0.05)
    return False
