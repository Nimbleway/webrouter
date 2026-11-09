"""The ``remote`` router and its wire format: names, environment variables, limits and messages.

Experimental: :mod:`web_router.routing` is new and may change before 1.0.
Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

REMOTE_ROUTER_NAME = "remote"
"""The registered name of the built-in router that asks an HTTP service: ``WebRouter(router="remote")``."""

EXTRA_REMOTE = "remote"
"""The extra that installs httpx, the ``remote`` router's one dependency. ``all`` includes it."""

ENV_REMOTE_ROUTER_API_KEY = "WEB_ROUTER_REMOTE_ROUTER_API_KEY"
"""The bearer key the ``remote`` router sends when ``router_config`` gives none."""

ENV_REMOTE_ROUTER_URL = "WEB_ROUTER_REMOTE_ROUTER_URL"
"""The MCP server's ``remote`` router URL, read once from its process environment. The library takes ``url``."""

REMOTE_TIMEOUT_S = 1.0
"""Seconds the ``remote`` router waits for its service when ``router_config`` sets no ``timeout_s``."""

REMOTE_MAX_RESPONSE_BYTES = 64 * 1024
"""The largest answer read from a router service: a decision is a few hundred bytes. Past it, a router error."""

REMOTE_CLOSE_WAIT_S = 2.0
"""Seconds a close from another thread waits for the pool to close on its own loop."""

REMOTE_JSON_TYPE = "application/json"
"""The one ``Content-Type`` a decision is read from (parameters such as ``charset`` aside)."""

REMOTE_LOOPBACK_NAME = "localhost"
"""The one name plain ``http`` may reach. ``foo.localhost`` and ``0.0.0.0`` are not exempt: a resolver may send
the first anywhere, and the second is no loopback address."""

REMOTE_URL_POLICY = "public_only"
"""The URL policy a router service's options are held to when the caller sets none: a service may not point
extract or browse at loopback, private or metadata hosts. A caller's own policy applies when it sets one."""

REMOTE_SECURE_SCHEME = "https"
REMOTE_LOOPBACK_SCHEME = "http"
"""``https`` everywhere; plain ``http`` only to a loopback host, such as a router on this machine."""

REMOTE_AUTH_HEADER = "Authorization"
REMOTE_AUTH_VALUE = "Bearer {key}"
REMOTE_CONTENT_TYPE_HEADER = "Content-Type"
REMOTE_MEDIA_PARAMS = ";"
"""What separates a media type from its parameters, as ``application/json; charset=utf-8``."""
REMOTE_CONTENT_TYPE = {"Content-Type": "application/json", "Accept": "application/json"}

REMOTE_DISPLAY_NAME = "remote({host})"
"""How a decision record and the MCP capabilities resource name the router: its URL's scheme and host only."""

WIRE_SCHEMA_KEY = "schema"
"""The field that carries the wire format's major version, on a request and on a decision."""

WIRE_SCHEMA_VERSION = 1
"""The wire format's major version. A decision of another major version is a router error."""

WIRE_SCHEMA_DIR = ("data", "schemas", "router", "v1")
"""Where the JSON Schema files live inside the package, under ``web_router``."""

WIRE_CONFIDENCE_RANGE = {"minimum": 0, "maximum": 1}
"""What the response schema adds to ``confidence``: the merge rule sets aside a decision outside 0..1."""

WIRE_MAX_TEXT = 300
"""Characters kept of a decision's ``reason`` and of each ``exclude`` reason; the rest is cut."""

WIRE_MAX_NAMES = 64
"""The most entries a decision may hold in one list or mapping: ``tools``, one method's ``providers``,
``exclude``, or one method's ``params``. Past it, the decision is refused."""

WIRE_REQUEST_FILE = "request.json"
WIRE_RESPONSE_FILE = "response.json"
WIRE_JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
WIRE_REQUEST_TITLE = "web-router RouteRequest, wire schema v1"
WIRE_RESPONSE_TITLE = "web-router RouteDecision, wire schema v1"

MSG_REMOTE_NO_URL = "router='remote' needs router_config={{'url': ...}}, the router service's URL."
MSG_REMOTE_URL = "router_config['url'] is not an absolute http(s) URL with a host that parses one way."
MSG_REMOTE_URL_PARTS = "router_config['url'] may not carry credentials, a query or a fragment: pass the key as api_key."
MSG_REMOTE_HTTPS = "router_config['url'] must use https; plain http is allowed only to localhost, 127.0.0.0/8 or ::1."
MSG_REMOTE_TIMEOUT = "router_config['timeout_s'] must be a positive number of seconds; got {value!r}."
MSG_REMOTE_KEY = "router_config['api_key'] must be a string."
MSG_REMOTE_NO_HTTPX = (
    "router='remote' needs httpx, which web-router's '{extra}' extra installs: pip install 'web-router[{extra}]'."
)
MSG_REMOTE_FAILED = "The router service at {host} failed: {why}."
REMOTE_WHY_TRANSPORT = "no answer ({kind})"
REMOTE_WHY_STATUS = "HTTP {status}"
REMOTE_WHY_TOO_LARGE = "an answer over {limit} bytes"
REMOTE_WHY_NOT_JSON = "an answer that is not a JSON object"
REMOTE_WHY_CONTENT_TYPE = "an answer that is not application/json"
REMOTE_WHY_TOO_MANY = "a decision with more than {limit} entries in one field"
REMOTE_WHY_SCHEMA = "a wire schema that is missing or not {expected}, the one this client reads"
REMOTE_WHY_DECISION = "a decision that does not fit the wire schema"

REMOTE_HTTP_OK = 200
"""The one status a decision is read from; a redirect is never followed, so a 3xx is a router error too."""
