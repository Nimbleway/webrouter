"""A router service in the standard library: what `router="remote"` asks, answered by the built-in rules router.

`WebRouter(router="remote", router_config={"url": ...})` posts each routed
call's `RouteRequest` as JSON and reads a `RouteDecision` back -- the wire
format, schema 1, whose JSON Schema files ship in the package under
`web_router/data/schemas/router/v1/`. This server implements it in a few
lines: it checks the bearer key, reads the request, asks
`web_router.routing.rules.rules` (a URL extracts, anything else searches) and
answers. Swap `decide` for your own logic, or port it to any language: a
service reads `"schema": 1` JSON, ignores request fields it does not know, and
answers `{"schema": 1, ...}` with any of the decision's optional fields.

What a service is shown: the prompt, the URLs in it, the budget and the
candidates, named `tool:provider` -- which say what backends the caller has
configured. Never a key value: the one credential on the request is the
service's own bearer key.

Run it with::

    make setup   # in your webrouter clone: every extra
    uv run python examples/router_remote_server.py            # serve on http://127.0.0.1:8765/route
    uv run python examples/router_remote_server.py --demo     # serve, plan three prompts through it, stop

Set `WEB_ROUTER_REMOTE_ROUTER_API_KEY` before starting it to require that key;
the `remote` router reads the same variable. It binds 127.0.0.1 unless
`--host` says otherwise (any other host needs the key), answers only a `Host`
header naming that address or `localhost`, caps a request at 1 MB, serves at
most 32 connections at once and drops one that sends nothing for 10 seconds.
Plain `http` is accepted only to `localhost` or a loopback address: put a real
service behind `https`.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import socket
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from web_router import WebRouter
from web_router.routing.rules import rules
from web_router.routing.wire import decision_to_wire, request_from_wire

PATH = "/route"
MAX_BODY_BYTES = 1_000_000
POLL_S = 0.05  # how soon shutdown() is noticed
SOCKET_TIMEOUT_S = 10.0
MAX_WORKERS = 32  # connections served at once; more are closed before a thread is spent on them
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
PROMPTS = (
    "what is retrieval augmented generation",
    "read https://en.wikipedia.org/wiki/Web_scraping",
    "compare the EU and US rules on AI training data",
)

Decide = Callable[[dict[str, Any]], dict[str, Any]]


def decide(body: dict[str, Any]) -> dict[str, Any]:
    """One request in, one decision out: the wire format around the built-in rules router."""
    return decision_to_wire(rules(request_from_wire(body)))


class RouterServer(ThreadingHTTPServer):
    """A router service: POST `/route`, a schema-1 request in, a schema-1 decision out."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], *, api_key: str | None = None, decide: Decide = decide) -> None:
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        super().__init__(address, _Handler)
        self.api_key = api_key
        self.decide = decide
        self._slots = threading.BoundedSemaphore(MAX_WORKERS)

    def process_request(self, request: Any, client_address: Any) -> None:
        """Serve a connection on its own thread while a slot is free; close it otherwise."""
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()  # no thread started, so none will give the slot back
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    address_family = socket.AF_INET  # set per instance below: AF_INET6 for an IPv6 host such as ::1

    @property
    def authority(self) -> str:
        """``host:port`` as a URL and a ``Host`` header write it: an IPv6 address in brackets."""
        host, port = self.server_address[:2]
        return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"

    @property
    def hosts(self) -> set[str]:
        """The ``Host`` headers it answers: the address it is bound to, and ``localhost`` on the same port."""
        return {self.authority, f"localhost:{self.server_address[1]}"}

    @property
    def url(self) -> str:
        return f"http://{self.authority}{PATH}"


class _Handler(BaseHTTPRequestHandler):
    server: RouterServer
    timeout = SOCKET_TIMEOUT_S  # a client that sends headers and then nothing gives up its thread

    def do_POST(self) -> None:
        if self.headers.get("Host") not in self.server.hosts:
            return self._answer(421, {"error": "unknown host"})  # a page re-pointing a DNS name here, say
        if self.path != PATH:
            return self._answer(404, {"error": "not found"})
        if not self._authorized():
            return self._answer(401, {"error": "a bearer key is required"})
        length = self.headers.get("Content-Length", "")
        if not (length.isascii() and length.isdigit()) or len(length) > 7 or int(length) > MAX_BODY_BYTES:
            return self._answer(413, {"error": "a request needs a Content-Length of at most 1 MB"})
        try:
            body = json.loads(self.rfile.read(int(length)))
            request_from_wire(body)  # checked apart from decide(), whose own errors are the server's, never a 400
        except (ValueError, RecursionError):  # bad JSON or a WireError, or JSON nested deeper than Python parses
            return self._answer(400, {"error": "not a schema-1 request"})
        try:
            return self._answer(200, self.server.decide(body))
        except Exception:  # a decide() that fails: an answer, not a dropped connection, and never its text
            return self._answer(500, {"error": "the router failed"})

    def _authorized(self) -> bool:
        if not self.server.api_key:
            return True
        given = self.headers.get("Authorization", "")
        return hmac.compare_digest(given.encode(), f"Bearer {self.server.api_key}".encode())

    def _answer(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: a request line is enough noise in tests, and headers -- the key among them -- are never logged."""


def serve(host: str = "127.0.0.1", port: int = 8765, *, api_key: str | None = None) -> RouterServer:
    """A server bound to ``host:port`` (0 picks a free port), answering on a background thread."""
    if not api_key and host not in LOOPBACK_HOSTS:
        raise ValueError("a host other than loopback needs WEB_ROUTER_REMOTE_ROUTER_API_KEY: the Host check is no auth")
    server = RouterServer((host, port), api_key=api_key)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": POLL_S}, daemon=True).start()
    return server


def demo(server: RouterServer) -> None:
    """Plan three prompts through the server, as any caller of `router="remote"` would."""
    with WebRouter(router="remote", router_config={"url": server.url, "api_key": server.api_key}) as router:
        for prompt in PROMPTS:
            plan = router.plan(prompt)
            print(f"{prompt!r}\n  -> {plan.tool}  ({plan.decision.router}: {plan.decision.reason})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true", help="plan three prompts through the server, then stop")
    args = parser.parse_args()
    api_key = os.environ.get("WEB_ROUTER_REMOTE_ROUTER_API_KEY") or None
    server = serve(args.host, 0 if args.demo else args.port, api_key=api_key)
    try:
        if args.demo:
            demo(server)
            return
        print(f"router service on {server.url}; stop with Ctrl-C")
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
