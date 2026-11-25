"""LiteParse: the formats it returns and the URLs extract ``auto`` sends it.

Kept apart from :mod:`web_router.constants`, as the other newer backends' are,
so that file stops growing. The provider name stays there, beside every other
backend's.
"""

from __future__ import annotations

from web_router.types import ExtractFormat

LITEPARSE_EXTRACT_FORMATS: dict[ExtractFormat, str] = {"markdown": "markdown", "text": "text"}
"""Our format to LiteParse's ``output_format``. Its third, ``json``, is a layout dump rather than a page body."""

LITEPARSE_EXTRACT_DEFAULT_FORMAT: ExtractFormat = "markdown"

LITEPARSE_ACCEPT = "*/*"
"""Sent instead of the JSON ``Accept`` every API provider sends: this fetches the file itself."""

DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"})
"""URL path endings extract ``auto`` hands its document parser before the web chain.

PDFs parse in process. Office files need LibreOffice on the machine; without it
the parse fails and the URL goes on to the web chain like any other.
"""

LITEPARSE_GONE_STATUSES = frozenset({404, 410})
"""Statuses that are the file's own: no backend would fetch it either. A 403 or 429 may be this machine's, so not."""

MSG_LITEPARSE_FAILED = "LiteParse could not read the file at {url}: {error}"

LITEPARSE_MAX_BYTES = 50 * 1024 * 1024
"""The largest file LiteParse downloads; a longer body fails before it fills memory."""

LITEPARSE_MAX_REDIRECTS = 5
"""Hops followed to reach the file: links to storage often redirect once or twice."""

LITEPARSE_PARSE_TIMEOUT = 60.0
"""The longest one parse may run when the call sets no deadline. The parse runs in a worker process killed after it."""

LITEPARSE_MIN_PARSE_SECONDS = 1.0
"""A parse is given at least this long even with less of the call left: a worker cannot start in less."""

MSG_LITEPARSE_TOO_LARGE = "The file at {url} is larger than LiteParse reads ({limit} bytes)"
MSG_LITEPARSE_REDIRECTS = "The file at {url} redirected more than {limit} times"
MSG_LITEPARSE_PRIVATE_HOP = "The file at {url} redirected to a host it may not reach: {why}"
MSG_LITEPARSE_UNRESOLVED = "the host name does not resolve here"
MSG_LITEPARSE_PRIVATE_NAME = "The file at {url} names a host LiteParse may not reach: {why}"
