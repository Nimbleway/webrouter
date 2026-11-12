"""Constants for vendor data: the catalog's location, its limits, and every message it raises.

Kept out of :mod:`web_router.constants`, which is already the largest file in
the package, the way the other method families keep their own.
"""

from __future__ import annotations

import re
from typing import Final

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

DATA_API_SEARCH_LIMIT: Final = 3
"""Data APIs :meth:`~web_router.WebRouter.search` lists beside the web results when ``max_results`` is not set."""

DATA_API_MIN_SCORE: Final = 3
"""The least match score a data API needs to be listed by ``search``: one query word in its id, job or name.

A word that only appears in an endpoint's summary or category scores 1, which
is too weak to list beside every web search, since discovery is on by default.
"""

DATA_APIS_ONLY_PROVIDER: Final = "data-apis"
"""``SearchResponse.provider`` for a ``search(data_apis_only=True)``: the local catalog answered, no web backend."""

MSG_DATA_APIS_ONLY_CONFLICT: Final = (
    "search(data_apis_only=True) calls no web backend, so it does not take {names}; drop them, or search the web."
)
MSG_DATA_APIS_ONLY_OFF: Final = "search(data_apis_only=True) needs data_apis=True: it lists data APIs and nothing else."

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
VENDOR_TEXT_CONTENT_MARKERS: Final = ("text/", "json", "xml", "javascript", "csv", "yaml", "x-www-form-urlencoded")
"""A content type holding one of these is decoded as text; any other non-empty body is kept as bytes."""

VENDOR_DROPPED_RESPONSE_HEADERS: Final = frozenset({"set-cookie"})
"""Response headers never handed back: a vendor's session cookie is a credential the caller did not ask for."""

VENDOR_ID_PATTERN: Final = r"^[a-z0-9][a-z0-9-]*$"
"""A vendor slug: lowercase, digits and dashes, the file's own name."""

VENDOR_ENDPOINT_ID_PATTERN: Final = r"^[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9_.-]*$"
"""``<vendor>.<job>``: the vendor slug, a dot, then dot-separated lowercase words."""

VENDOR_ENV_PATTERN: Final = r"^[A-Z][A-Z0-9_]*$"

VENDOR_CATEGORY_DESCRIPTIONS: Final = {
    "advertising": "Ad accounts and ad libraries: campaigns, spend, creatives.",
    "analytics": "Web and product analytics, tag management, CRM and marketing data.",
    "seo": "Keywords, rankings, backlinks and search-console data.",
    "social": "Social networks and communities: profiles, posts, creators.",
    "enrichment": "People and contact data: emails, phones, profiles.",
    "verification": "Checking an email, a phone number or an identity.",
    "company-data": "Companies, funding, jobs, technographics and business registries.",
    "finance": "Markets, crypto, filings and economic statistics.",
    "search": "Web, news and archive search.",
    "scraping": "Fetching, rendering and extracting web pages; hosted browsers.",
    "ai": "AI model benchmarks, model hubs and computed answers.",
    "media": "Stock images, video, music, films and texts.",
    "messaging": "Your email, SMS, call and chat history.",
    "productivity": "Calendars, files and documents.",
    "commerce": "Products, prices, travel, tickets, property and shipping.",
    "maps": "Maps, places, routing and geolocation.",
    "environment": "Weather, climate, water and natural hazards.",
    "science": "Research literature and life-science databases: genes, proteins, compounds.",
    "health": "Drugs, clinical trials, providers, nutrition and public-health data.",
    "government": "Public records: law, courts, spending, procurement, sanctions, elections, open-data portals.",
    "sports": "Fantasy sports, leagues and sports statistics.",
    "security": "Vulnerabilities, advisories and threat intelligence.",
    "developer": "Package registries, code hosting, cloud platforms and their prices.",
    "observability": "Logs, metrics, errors and uptime.",
    "custom": "Vendors from your own catalog directory (WEB_ROUTER_VENDOR_CATALOG) that fit no shipped shelf.",
}
"""Each shelf a vendor is listed under, with what belongs on it; ``VENDORS.md`` prints the line under its heading."""

VENDOR_CATEGORIES: Final = tuple(VENDOR_CATEGORY_DESCRIPTIONS)
"""The shelves a vendor is listed under, one per vendor. There is no catch-all for shipped vendors: one that
fits none needs a new shelf."""

MSG_VENDOR_OAUTH_LITERAL: Final = (
    "oauth2.{field} is sent as written and cannot hold a placeholder: {value!r}. Use the literal value."
)

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
MSG_VENDOR_BAD_PATH: Final = (
    "{path!r} is not a path on {vendor}'s API: pass a path beginning with '/', with no scheme or host. "
    "Credentials are only ever sent to {base_url}."
)
MSG_VENDOR_HOST_ESCAPE: Final = (
    "Refusing to send {vendor} credentials to {host!r}: the vendor's API is on {expected!r}."
)
MSG_VENDOR_SETTING_CHOICE: Final = "{vendor} setting {name} must be one of {choices}, not {value!r} (from {source})."
VENDOR_EMPTY_BODY: Final = "(empty body)"
"""What a failed call's message shows when the vendor answered with no body."""
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
VENDOR_CALLBACK_KEY: Final = re.compile(r"webhook|callback|pingback|postback", re.IGNORECASE)
"""A key inside an object input that names one is refused: it would send the answer to an address the caller picks."""
MSG_VENDOR_CALLBACK_REFUSED: Final = (
    "Callback and webhook URLs are left out of vendor calls; poll for the result instead."
)
MSG_VENDOR_CATALOG_WHEN: Final = "injection {injection!r}: when {name}={value!r} names no choice of a setting"
MSG_VENDOR_CATALOG_UNKNOWN_REF: Final = (
    "auth references {{{name}}}, which is neither a credential, a setting nor access_token"
)
MSG_VENDOR_CATALOG_BASE_URL_REF: Final = "base_url references {{{name}}}, which is not a setting"
MSG_VENDOR_CATALOG_SETTING_CHOICES: Final = (
    "base_url setting {name!r} must declare choices, so a credential cannot be sent to an arbitrary host"
)
MSG_VENDOR_CATALOG_SCHEME: Final = "base_url must be https: {url!r}"
MSG_VENDOR_CATALOG_ACCESS_TOKEN: Final = "auth references {access_token} but declares no oauth2 block"

EXTRA_VENDORS = "vendors"
"""The extra that installs what a vendor call needs (httpx)."""
