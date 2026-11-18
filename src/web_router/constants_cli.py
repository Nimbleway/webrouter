"""What the ``webrouter`` command line prints and how it exits.

Kept apart from :mod:`web_router.constants`, so that file stops growing. Every
string a caller reads lives here, so the command's help, its errors and its
exit codes can be checked in one place.
"""

from __future__ import annotations

CLI_PROG = "webrouter"
"""The console script's name, as ``--help`` and every usage error print it."""

EXIT_OK = 0
EXIT_FAILED = 1
"""The call ran and failed: a backend error, a timeout, a router that found nothing to serve it."""
EXIT_USAGE = 2
"""Refused before anything was sent: a bad flag, an unknown backend, an unmapped option, bad JSON, a cost cap."""
EXIT_CONFIG = 3
"""This machine is not set up for the call: a missing key, or an extra not installed."""
EXIT_INTERRUPTED = 130
"""Stopped with Ctrl-C, 128 plus SIGINT as a shell reports it; a call may have started, so it may have billed."""

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
  2  refused before anything was sent (bad flag, unknown backend, unsupported option, bad JSON,
     nothing fits --max-cost)
  3  not set up for it (missing API key, extra not installed)
  130  stopped with Ctrl-C (a call may already have started)

keys:
  Read from the environment only. Run `webrouter providers` to see which backends are configured, and see
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
    "live-data sources": "List or search a vendor's catalogue of sources. Free.",
    "live-data source": "Describe one source: its inputs and output schema. Free.",
    "live-data run": "Run one source and return its records (bills per run).",
}
"""Each subcommand's one line in ``--help``; ``live-data``'s actions are keyed by their full command."""

HELP = {
    "provider": "Backend to use, e.g. tavily. Omit it and the router picks one (auto).",
    "option": "A backend-specific option, KEY=VALUE; VALUE is read as JSON when it parses. Repeatable.",
    "timeout": "Seconds to wait before giving up.",
    "max_cost": "Refuse a backend whose estimated cost would pass this many US dollars, before it runs.",
    "include_domain": "Only results from this domain. Repeatable.",
    "exclude_domain": "No results from this domain. Repeatable.",
    "country": "Two-letter country to search from, e.g. US.",
    "locale": "Language of the results, e.g. en.",
    "recency": "Only results from this recent period.",
    "max_results": "How many results to return.",
    "depth": "Speed against thoroughness.",
    "search_effort": "How much work the search does, on a backend that maps it.",
    "data_apis_only": "List only the matching data API endpoints from the local catalogue; call no web backend.",
    "no_data_apis": "Leave out data_apis, the catalogue endpoints matching the query that a search lists by default.",
    "configured_only": "List only the data API endpoints this machine holds credentials for.",
    "format": "Page representation (default markdown).",
    "research_effort": "How much work the run should do.",
    "schema": "JSON Schema for a structured answer, inline or @file.",
    "records": "Rows to enrich (needs --schema), inline or @file.",
    "start_url": "The page the agent starts on.",
    "max_steps": "Most actions the agent may take.",
    "allowed_domain": "Keep the agent on this domain. Repeatable.",
    "catalogue_query": "Search the catalogue, e.g. 'amazon product'.",
    "limit": "Sources per page.",
    "offset": "Sources to skip.",
    "input": "One of the source's inputs, KEY=VALUE, sent as text. Repeatable; --inputs for typed values.",
    "inputs": "All of the source's inputs as one JSON object, inline or @file.",
    "live_format": "Also return the page in this format.",
    "raw": "Include the backend's untouched payload (raw) in the output.",
    "pretty": "Indent the JSON output.",
}
"""Every flag's help, keyed by what the flag sets."""

MSG_BAD_PAIR = "expected KEY=VALUE, got {value!r}"
MSG_BAD_JSON = "{what} is not valid JSON: {error}"
MSG_NOT_FINITE = "{what} must be a finite number, got {value}"
MSG_NOT_OBJECT = "{what} must be a JSON object"
MSG_NOT_LIST = "{what} must be a JSON array of objects"
MSG_BAD_NUMBER = "invalid {kind} value: {value!r}"
MSG_NOT_POSITIVE = "must be a finite number above 0, got {value}"
MSG_NEGATIVE = "must be 0 or more, got {value}"
MSG_UNREADABLE = "cannot read {path}: {error}"
