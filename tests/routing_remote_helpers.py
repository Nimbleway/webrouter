"""What the remote router's tests share: the reference server from examples/, started on a free loopback port."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests.examples.helpers import load_example

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
    """What a hand-written server answers, and every request it was sent: path, headers."""

    def __init__(
        self, status: int = 200, body: bytes = b"{}", headers: dict[str, str] | None = None, drip_s: float = 0.0
    ) -> None:
        self.status, self.body, self.drip_s = status, body, drip_s
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.seen: list[tuple[str, dict[str, str]]] = []


@contextmanager
def canned(answer: Canned) -> Iterator[str]:
    """A server on a free loopback port that answers every request with ``answer``; yields its base URL."""

    class Handler(BaseHTTPRequestHandler):
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
