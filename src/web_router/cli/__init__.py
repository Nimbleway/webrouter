"""The ``webrouter`` command line: every router method as a subcommand that prints JSON.

For agents and scripts that run shell commands rather than speak MCP. Each
subcommand calls the public method a library caller would, so it validates,
routes and fails exactly as the library does::

    $ webrouter search "rust async runtimes" -n 5
    $ webrouter extract https://example.com --format markdown
    $ webrouter providers

stdout carries one JSON object, the response; an error leaves stdout empty and
prints ``{"error": {"type", "message"}}`` on stderr, with an exit status that
says whether the call failed, was refused, or this machine is not set up for it.
Keys come from the environment only, as everywhere else in the library.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from web_router.cli.commands import HANDLERS
from web_router.cli.output import to_json, write_error
from web_router.cli.parser import UsageError, build_parser
from web_router.client import WebRouter
from web_router.constants_cli import EXIT_OK

__all__ = ["main", "run"]


def run(argv: Sequence[str] | None = None) -> int:
    """Run one command line and return its exit status; :func:`main` is this plus ``sys.exit``."""
    try:
        args = build_parser().parse_args(argv)
    except UsageError as exc:
        return write_error(exc, sys.stderr)
    router = WebRouter()
    try:
        response = HANDLERS[args.command](router, args)
    except (Exception, KeyboardInterrupt) as exc:  # every failure, Ctrl-C too, is the JSON envelope, never a traceback
        return write_error(exc, sys.stderr)
    finally:
        router.close()
    try:
        print(to_json(response, raw=args.raw, pretty=args.pretty), flush=True)
    except BrokenPipeError:
        # A reader that stopped early (`| head`) is not an error; point stdout at devnull so exit's flush is quiet.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return EXIT_OK


def main() -> None:
    """The ``webrouter`` console script."""
    sys.exit(run())
