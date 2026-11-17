"""What the ``web-router`` command line prints and how it exits.

Kept apart from :mod:`web_router.constants`, so that file stops growing. Every
string a caller reads lives here, so the command's help, its errors and its
exit codes can be checked in one place.
"""

from __future__ import annotations

CLI_PROG = "web-router"
"""The console script's name, as ``--help`` and every usage error print it."""

EXIT_OK = 0
EXIT_FAILED = 1
"""The call ran and failed: a backend error, a timeout, a router that found nothing to serve it."""
EXIT_USAGE = 2
"""Refused before anything was sent: a bad flag, an unknown backend, an option the backend does not map, bad JSON."""
EXIT_CONFIG = 3
"""This machine is not set up for the call: a missing key, or an extra not installed."""

CLI_JSON_INDENT = 2
"""Indent under ``--pretty``; without it the output is one line, which is what a pipe or an agent wants."""

CLI_OUTPUT_EXCLUDED = frozenset({"raw"})
"""Response fields left out unless ``--raw`` asks for them: the backend's untouched payload, often the bulk of it."""

CLI_FILE_PREFIX = "@"
"""A JSON argument starting with this names a file to read it from, as curl's ``-d @file`` does."""

CLI_USAGE_ERROR = "UsageError"
"""The ``type`` an error printed by the argument parser carries, since no exception class names it."""

CLI_DESCRIPTION = (
    "One command line in front of many web APIs: search, extract, answer, research, browse and live data, "
    "each behaving the same whichever backend serves it."
)

CLI_EPILOG = """\
output:
  stdout is one JSON object, the call's response (the backend's untouched payload only with --raw).
  On failure stdout is empty and stderr holds {"error": {"type": ..., "message": ...}}.

exit codes:
  0  success
  1  the call ran and failed (backend error, timeout, nothing could serve it)
  2  refused before anything was sent (bad flag, unknown backend, unsupported option, bad JSON)
  3  not set up for it (missing API key, extra not installed)

keys:
  Read from the environment only. Run `web-router providers` to see which backends are configured, and see
  .env.example for every variable. With no key at all, search and extract still work through keyless backends.

cost:
  search, extract and answer answer in seconds. research and browse are jobs of minutes and bill per run;
  live-data runs a vendor's scraper on your account. Omit --provider and the router picks the backend.
"""

HELP_COMMAND = {
    "search": "Search the web; ranked results with titles, URLs and text.",
    "extract": "Fetch the contents of one or more URLs you already have.",
    "answer": "One question, one written answer with the sources it rests on (seconds, bills per call).",
    "research": "An agentic research run: a report or structured records (minutes, bills per run).",
    "browse": "A hosted browser agent carries out a task on the web (minutes, bills per run).",
    "live-data": "A vendor's ready-made scraper for one site: list sources, describe one, or run it.",
    "providers": "The backends this machine is configured for, per method. Free; calls nothing.",
}
"""Each subcommand's one line in ``web-router --help``."""

HELP_PROVIDER = "Backend to use, e.g. tavily. Omit it and the router picks one (auto)."
HELP_OPTION = "A backend-specific option, KEY=VALUE; VALUE is read as JSON when it parses. Repeatable."
HELP_TIMEOUT = "Seconds to wait before giving up."
HELP_INCLUDE = "Only results from this domain. Repeatable."
HELP_EXCLUDE = "No results from this domain. Repeatable."
HELP_SCHEMA = "JSON Schema for a structured answer, inline or @file."
HELP_RAW = "Include the backend's untouched payload (raw) in the output."
HELP_PRETTY = "Indent the JSON output."

MSG_BAD_PAIR = "expected KEY=VALUE, got {value!r}"
MSG_BAD_JSON = "{what} is not valid JSON: {error}"
MSG_NOT_OBJECT = "{what} must be a JSON object"
MSG_NOT_LIST = "{what} must be a JSON array of objects"
MSG_UNREADABLE = "cannot read {path}: {error}"
