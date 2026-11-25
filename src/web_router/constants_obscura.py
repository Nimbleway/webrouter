"""Obscura extract: the command it runs, the formats it maps and the messages it raises.

Kept apart from :mod:`web_router.constants`, so that file stops growing. The
provider's name and its variable stay there, beside the other tables. Checked
against ``obscura fetch --help`` of release v0.2.7 (github.com/h4ckf0r0day/obscura).
"""

from __future__ import annotations

from web_router.types import ExtractFormat

OBSCURA_COMMAND = "obscura"
"""The executable looked up on ``PATH`` when neither ``executable_path`` nor ``OBSCURA_PATH`` names one."""

ENV_OBSCURA_ALLOW_PRIVATE_NETWORK = "OBSCURA_ALLOW_PRIVATE_NETWORK"
"""The binary's private-network opt-out, removed from each fetch's environment so its guard stays on."""

OBSCURA_EXTRACT_FORMATS: dict[ExtractFormat, str] = {"markdown": "markdown", "html": "html", "text": "text"}
"""Normalized format -> the ``--dump`` value that prints it, after the page's scripts have run."""

OBSCURA_EXTRACT_DEFAULT_FORMAT: ExtractFormat = "markdown"

OBSCURA_FETCH_ARGV = ("fetch", "--dump", "{dump}", "--quiet", "--timeout", "{timeout}", "--", "{url}")
"""The arguments after the binary, each filled in with ``str.format``; ``--`` keeps a URL from reading as a flag."""

OBSCURA_SCHEMES = frozenset({"http", "https"})
"""The URL schemes sent to the binary. ``obscura fetch`` reads ``file://`` URLs too, so any other is refused first."""

OBSCURA_FETCH_TIMEOUT = 30.0
"""Seconds one fetch may take when no deadline is set: the binary's own default, sent as ``--timeout``."""

OBSCURA_EXIT_GRACE = 5.0
"""With no deadline, seconds past ``--timeout`` before a process that has not stopped on its own is killed."""

OBSCURA_MAX_REPLACEMENT_SHARE = 0.01
"""Past this share of U+FFFD characters, the output is a file, not a page.

Fetched a PDF or an image, ``obscura fetch`` prints its bytes as text with each undecodable one replaced (about 13%
of a PDF's output); a rendered page has next to none.
"""

OBSCURA_PDF_MAGIC = "%PDF-"
"""How a PDF's bytes begin. ``obscura fetch`` prints a PDF as it came, never rendered, however much of it decodes."""

OBSCURA_STDERR_TAIL = 500
"""Characters of the binary's error output kept in a failure's message."""

MSG_OBSCURA_NOT_FOUND = (
    "obscura: no `obscura` binary found. Install a release from https://github.com/h4ckf0r0day/obscura/releases, "
    "then put it on PATH or set {env_var} to its path."
)
MSG_OBSCURA_BAD_PATH = "obscura: {path!r} is not an executable file; check executable_path or {env_var}."
MSG_OBSCURA_SCHEME = "only http and https URLs are fetched, and this one has {scheme}"
MSG_OBSCURA_NO_SCHEME = "no scheme"
MSG_OBSCURA_EXITED = "exited with code {code}: {detail}"
MSG_OBSCURA_TIMED_OUT = "did not finish within {seconds:g}s"
MSG_OBSCURA_EMPTY = "returned no content"
MSG_OBSCURA_NOT_TEXT = "returned a file that is not a web page (a PDF, an image), not text"
MSG_OBSCURA_NO_PROVIDER_OPTIONS = (
    "obscura passes no provider_options on: its own fetch flags are not mapped, and some of them "
    "(--allow-private-network, --eval) widen what a fetch may reach or run."
)
