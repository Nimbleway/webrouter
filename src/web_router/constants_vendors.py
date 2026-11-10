"""Constants for vendor data: the catalog's location, its limits, and every message it raises.

Kept out of :mod:`web_router.constants`, which is already the largest file in
the package, the way the other method families keep their own.
"""

from __future__ import annotations

from typing import Any, Final

VENDOR_CATALOG_DIR: Final = "data/vendors"
"""Inside the ``web_router`` package: one JSON file per vendor."""

VENDOR_ENV_CATALOG_DIR: Final = "WEB_ROUTER_VENDOR_CATALOG"
"""A directory of extra vendor files, read after the built-in ones.

A file there for a vendor the package already ships replaces it whole, so a
team can pin a version or add a vendor without forking.
"""

VENDOR_ENV_ALLOW_WRITES: Final = "WEB_ROUTER_ALLOW_VENDOR_WRITES"
"""Set to ``1`` to let the MCP server's ``call_vendor`` send writes. The library takes ``allow_writes=`` per call."""

VENDOR_HTTP_TIMEOUT: Final = 60.0
"""Seconds per vendor call. Reporting APIs (GAQL, log search) are slower than a search backend."""

VENDOR_ERROR_BODY_LIMIT: Final = 2000
"""Characters of a vendor's error body kept in the raised message, after credentials are masked."""

VENDOR_FIND_LIMIT: Final = 20
"""Endpoints :meth:`~web_router.WebRouter.find_vendor_endpoints` returns when no limit is given."""

VENDOR_FIND_STOP_WORDS: Final = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "the",
        "to",
        "what",
        "with",
        "your",
    }
)
"""Query words :meth:`~web_router.WebRouter.find_vendor_endpoints` ignores: they say nothing about what an
endpoint does, and counted as matches they let a summary's grammar outrank its meaning."""

VENDOR_TOKEN_REFRESH_MARGIN: Final = 60.0
"""Seconds before an OAuth access token's stated expiry at which it is refreshed instead of reused."""

VENDOR_TOKEN_DEFAULT_LIFETIME: Final = 3000.0
"""Seconds an access token is reused when the token endpoint states no ``expires_in``."""

VENDOR_READ_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
"""Methods a raw :meth:`~web_router.WebRouter.vendor_request` may send without ``allow_writes``."""

VENDOR_MASK: Final = "***"

VENDOR_MASK_INSIDE_MIN: Final = 8
"""Shortest secret masked *inside* a longer string of a successful answer.

An error body is masked for every secret, however short: it is text about
the failure. A successful answer is the caller's data, so a short value there
(a login, a client id) is masked only where a whole string equals it --
replacing every "user" inside every string would corrupt the answer.
"""

VENDOR_REQUEST_HEADERS: Final = {"Accept": "application/json"}
"""Sent on every vendor call, before the vendor's own headers, which may override them."""

VENDOR_CONTENT_TYPE_HEADER: Final = "content-type"

VENDOR_DROPPED_RESPONSE_HEADERS: Final = frozenset({"set-cookie"})
"""Response headers never handed back: a vendor's session cookie is a credential the caller did not ask for."""

VENDOR_ID_PATTERN: Final = r"^[a-z0-9][a-z0-9-]*$"
"""A vendor slug: lowercase, digits and dashes, the file's own name."""

VENDOR_ENDPOINT_ID_PATTERN: Final = r"^[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9_.-]*$"
"""``<vendor>.<job>``: the vendor slug, a dot, then dot-separated lowercase words."""

VENDOR_ENV_PATTERN: Final = r"^[A-Z][A-Z0-9_]*$"

VENDOR_CATEGORIES: Final = (
    "advertising",
    "analytics",
    "seo",
    "observability",
    "social",
    "enrichment",
    "email-verification",
    "company-data",
    "finance",
    "search",
    "scraping",
    "ai",
    "media-generation",
    "messaging",
    "developer",
    "other",
)
"""The shelves a vendor is listed under. One per vendor; ``other`` when none fits."""

MSG_VENDOR_UNKNOWN: Final = "Unknown vendor {vendor!r}. Known vendors: {known}"
MSG_VENDOR_ENDPOINT_UNKNOWN: Final = "Unknown vendor endpoint {endpoint!r}.{hint}"
MSG_VENDOR_ENDPOINT_HINT: Final = " {vendor!r} has: {known}"
MSG_VENDOR_ENDPOINT_NO_VENDOR: Final = " No vendor is named {vendor!r}; endpoint ids are '<vendor>.<job>'."
MSG_VENDOR_MISSING_CREDENTIAL: Final = (
    "{vendor} needs {what}. Set {variables} in your environment or .env file, "
    "or pass vendor_credentials={{{vendor!r}: {{{name!r}: ...}}}} to the router."
)
MSG_VENDOR_WRITE_REFUSED: Final = (
    "{target} changes data on {vendor} and was refused: pass allow_writes=True to send it. "
    "Prefer the vendor's dry-run option first when it has one."
)
MSG_VENDOR_MCP_WRITE_REFUSED: Final = (
    "{target} changes data on {vendor}. This server refuses writes unless it was started with "
    + VENDOR_ENV_ALLOW_WRITES
    + "=1 and the call passes allow_writes=true. Ask the user before enabling either."
)
MSG_VENDOR_BAD_PATH: Final = (
    "{path!r} is not a path on {vendor}'s API: pass a path beginning with '/', with no scheme or host. "
    "Credentials are only ever sent to {base_url}."
)
MSG_VENDOR_HOST_ESCAPE: Final = (
    "Refusing to send {vendor} credentials to {host!r}: the vendor's API is on {expected!r}."
)
MSG_VENDOR_SETTING_CHOICE: Final = "{vendor} setting {name} must be one of {choices}, not {value!r} (from {source})."
MSG_VENDOR_HTTP_ERROR: Final = "HTTP {status} from {method} {path}: {body}"
MSG_VENDOR_TOKEN_ERROR: Final = "could not refresh the OAuth access token at {url}: HTTP {status}: {body}"
MSG_VENDOR_TOKEN_NO_ACCESS: Final = "the token endpoint at {url} answered without an access_token"
MSG_VENDOR_TOKEN_UNREADABLE: Final = "the token endpoint at {url} answered something that is not a token: {error}"
MSG_VENDOR_TOKEN_UNREACHABLE: Final = "could not reach the token endpoint at {url}: {error}"
MSG_VENDOR_CATALOG_INVALID: Final = "Vendor catalog file {file} is invalid: {error}"
MSG_VENDOR_CATALOG_ID_MISMATCH: Final = "declares vendor {declared!r} but is named {stem!r}.json"
MSG_VENDOR_CATALOG_DUPLICATE_ENDPOINT: Final = "endpoint id {endpoint!r} appears more than once"
MSG_VENDOR_CATALOG_ENDPOINT_PREFIX: Final = "endpoint id {endpoint!r} must start with {prefix!r}"
MSG_VENDOR_CATALOG_PATH_PARAM: Final = "endpoint {endpoint!r}: path placeholder {{{name}}} has no 'path' parameter"
MSG_VENDOR_CATALOG_PATH_UNUSED: Final = "endpoint {endpoint!r}: path parameter {name!r} is not in the path"
MSG_VENDOR_CATALOG_BODY_ON_GET: Final = "endpoint {endpoint!r}: a {method} request cannot carry body parameters"
MSG_VENDOR_CATALOG_UNKNOWN_REF: Final = (
    "auth references {{{name}}}, which is neither a credential, a setting nor access_token"
)
MSG_VENDOR_CATALOG_BASE_URL_REF: Final = "base_url references {{{name}}}, which is not a setting"
MSG_VENDOR_CATALOG_SETTING_CHOICES: Final = (
    "base_url setting {name!r} must declare choices, so a credential cannot be sent to an arbitrary host"
)
MSG_VENDOR_CATALOG_SCHEME: Final = "base_url must be https: {url!r}"
MSG_VENDOR_CATALOG_ACCESS_TOKEN: Final = "auth references {access_token} but declares no oauth2 block"

# -- MCP ------------------------------------------------------------------

MCP_TOOL_VENDORS: Final = "vendors"
MCP_TOOL_VENDOR_ENDPOINT: Final = "vendor_endpoint"
MCP_TOOL_CALL_VENDOR: Final = "call_vendor"

MCP_VENDORS_PROPERTIES: dict[str, dict[str, Any]] = {
    "query": {
        "type": "string",
        "description": "What you want, in words: 'google ads spend by campaign', 'datadog error logs'. "
        "Lists matching endpoints, best first. Unset, lists the vendors instead.",
    },
    "vendor": {"type": "string", "description": "Only this vendor's endpoints (its slug, e.g. 'google-ads')."},
    "category": {"type": "string", "enum": list(VENDOR_CATEGORIES), "description": "Only vendors on this shelf."},
    "configured": {
        "type": "boolean",
        "description": "When listing vendors: true for the ones whose credentials are set here, false for the rest.",
    },
    "limit": {"type": "integer", "minimum": 1, "description": "Most endpoints to list; 20 unset."},
}

MCP_VENDOR_ENDPOINT_PROPERTIES: dict[str, dict[str, Any]] = {
    "endpoint": {"type": "string", "description": "The endpoint id, '<vendor>.<job>', as 'vendors' lists it."},
}

MCP_CALL_VENDOR_PROPERTIES: dict[str, dict[str, Any]] = {
    "endpoint": {
        "type": "string",
        "description": "A catalog endpoint id. Its inputs go in 'params'. Omit it, and give 'vendor', 'method' "
        "and 'path', to call a path the catalog does not name.",
    },
    "params": {
        "type": "object",
        "description": "The endpoint's own inputs, named as vendor_endpoint lists them. An undeclared one is refused.",
    },
    "vendor": {"type": "string", "description": "For a raw call: the vendor's slug."},
    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "For a raw call."},
    "path": {"type": "string", "description": "For a raw call: a path on the vendor's API, starting with '/'."},
    "query": {"type": "object", "description": "For a raw call: query parameters."},
    "host": {
        "type": "string",
        "description": "For a raw call: one of the vendor's other named hosts, if not its main one.",
    },
    "body": {"description": "For a raw call: the JSON body."},
    "allow_writes": {
        "type": "boolean",
        "description": "Required for a call that changes data on the vendor's side. Ask the user first, and send "
        "the vendor's dry-run option (validateOnly and the like) before the real call.",
    },
}

MCP_DESC_VENDORS = (
    "Find a vendor API to call with the user's own credentials: Google Ads, Datadog and the rest of the vendor "
    "catalog. With 'query', lists matching endpoints, best first, each with its id, what it does, whether it "
    "reads or writes, and whether its vendor's credentials are set here. Without, lists the vendors. Then "
    "'vendor_endpoint' for an endpoint's inputs and 'call_vendor' to run it."
)
MCP_DESC_VENDOR_ENDPOINT = (
    "Describe one vendor endpoint: every input with where it goes and an example, the notes a caller gets "
    "wrong without (units, paging, quirks), and the credentials it needs. Read it before 'call_vendor'."
)
MCP_DESC_CALL_VENDOR = (
    "Call a vendor's API with the user's credentials and return its answer as the vendor sent it: field names, "
    "units and paging are the vendor's own. Credentials are added here and never shown. Calls that change "
    "data are refused unless 'allow_writes' is true and the server allows writes. The full answer stays "
    "behind the returned handle for 'fetch_raw' when it does not fit."
)

MCP_MSG_VENDOR_CALL_ARGS: Final = (
    "call_vendor needs either 'endpoint' (with 'params'), or 'vendor', 'method' and 'path' -- not both."
)
MCP_MSG_VENDOR_TRUNCATED: Final = "The answer was cut to fit; the whole of it is behind handle {handle!r} (fetch_raw)."

EXTRA_VENDORS = "vendors"
"""The extra that installs what a vendor call needs (httpx)."""

VENDOR_MCP_PROPERTIES: dict[str, dict[str, dict[str, Any]]] = {
    MCP_TOOL_VENDORS: MCP_VENDORS_PROPERTIES,
    MCP_TOOL_VENDOR_ENDPOINT: MCP_VENDOR_ENDPOINT_PROPERTIES,
    MCP_TOOL_CALL_VENDOR: MCP_CALL_VENDOR_PROPERTIES,
}
VENDOR_MCP_REQUIRED: dict[str, tuple[str, ...]] = {
    MCP_TOOL_VENDORS: (),
    MCP_TOOL_VENDOR_ENDPOINT: ("endpoint",),
    # `endpoint` or `vendor`+`method`+`path`: one-of, which a flat `required` cannot say; the tool checks it.
    MCP_TOOL_CALL_VENDOR: (),
}
VENDOR_MCP_DESCRIPTIONS: dict[str, str] = {
    MCP_TOOL_VENDORS: MCP_DESC_VENDORS,
    MCP_TOOL_VENDOR_ENDPOINT: MCP_DESC_VENDOR_ENDPOINT,
    MCP_TOOL_CALL_VENDOR: MCP_DESC_CALL_VENDOR,
}
"""The vendor tools' rows in :mod:`web_router.constants`' MCP tables, kept here so that file does not grow."""
