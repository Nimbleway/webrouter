"""The ``web-router`` argument parser: one subcommand per router method.

Choices come from the library's own ``Literal`` types, so a value the router
would refuse is refused here first, and ``--help`` lists what each flag takes.
"""

from __future__ import annotations

import argparse
from typing import NoReturn, get_args

from web_router.constants_cli import (
    CLI_DESCRIPTION,
    CLI_EPILOG,
    CLI_PROG,
    HELP_COMMAND,
    HELP_EXCLUDE,
    HELP_INCLUDE,
    HELP_OPTION,
    HELP_PRETTY,
    HELP_PROVIDER,
    HELP_RAW,
    HELP_SCHEMA,
    HELP_TIMEOUT,
)
from web_router.types import ExtractFormat
from web_router.types_options import Recency, ResearchEffort, SearchDepth


class UsageError(Exception):
    """The parser refused the command line; carries argparse's message so the caller can print it as JSON."""


class _Parser(argparse.ArgumentParser):
    """Raises instead of printing and exiting, so a usage error reaches stderr in the same JSON as any other."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(f"{self.prog}: {message}")


def _output_flags(parser: argparse.ArgumentParser, *, default: object) -> None:
    """``--pretty`` and ``--raw``, accepted before the command and after it, where agents tend to put them.

    A subcommand's copy defaults to ``SUPPRESS``, so leaving it out there never undoes the flag given before.
    """
    parser.add_argument("--pretty", action="store_true", default=default, help=HELP_PRETTY)
    parser.add_argument("--raw", action="store_true", default=default, help=HELP_RAW)


def build_parser() -> argparse.ArgumentParser:
    """The whole command line: global flags, then a subcommand per method."""
    parser = _Parser(
        prog=CLI_PROG,
        description=CLI_DESCRIPTION,
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _output_flags(parser, default=False)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND", parser_class=_Parser)
    _search(commands)
    _extract(commands)
    _answer(commands)
    _research(commands)
    _browse(commands)
    _live_data(commands)
    providers = commands.add_parser("providers", help=HELP_COMMAND["providers"], description=HELP_COMMAND["providers"])
    _output_flags(providers, default=argparse.SUPPRESS)
    return parser


def _command(commands: argparse._SubParsersAction[_Parser], name: str) -> _Parser:
    """A subcommand with the flags every call takes: the backend, its own options and a timeout."""
    sub = commands.add_parser(name, help=HELP_COMMAND[name], description=HELP_COMMAND[name])
    sub.add_argument("-p", "--provider", help=HELP_PROVIDER)
    sub.add_argument("--option", action="append", metavar="KEY=VALUE", help=HELP_OPTION)
    sub.add_argument("--timeout", type=float, metavar="SECONDS", help=HELP_TIMEOUT)
    _output_flags(sub, default=argparse.SUPPRESS)
    return sub


def _filters(sub: argparse.ArgumentParser) -> None:
    """The result filters search and answer share."""
    sub.add_argument("--include-domain", action="append", metavar="DOMAIN", help=HELP_INCLUDE)
    sub.add_argument("--exclude-domain", action="append", metavar="DOMAIN", help=HELP_EXCLUDE)
    sub.add_argument("--country", help="Two-letter country to search from, e.g. US.")
    sub.add_argument("--locale", help="Language of the results, e.g. en.")
    sub.add_argument("--recency", choices=get_args(Recency), help="Only results from this recent period.")


def _search(commands: argparse._SubParsersAction[_Parser]) -> None:
    sub = _command(commands, "search")
    sub.add_argument("query")
    sub.add_argument("-n", "--max-results", type=int, metavar="N", help="How many results to return.")
    sub.add_argument("--depth", choices=get_args(SearchDepth), help="Speed against thoroughness.")
    _filters(sub)


def _extract(commands: argparse._SubParsersAction[_Parser]) -> None:
    sub = _command(commands, "extract")
    sub.add_argument("urls", nargs="+", metavar="URL")
    sub.add_argument("--format", choices=get_args(ExtractFormat), help="Page representation (default markdown).")


def _answer(commands: argparse._SubParsersAction[_Parser]) -> None:
    sub = _command(commands, "answer")
    sub.add_argument("query")
    _filters(sub)


def _research(commands: argparse._SubParsersAction[_Parser]) -> None:
    sub = _command(commands, "research")
    sub.add_argument("query")
    sub.add_argument("--effort", choices=get_args(ResearchEffort), help="How much work the run should do.")
    sub.add_argument("--schema", metavar="JSON", help=HELP_SCHEMA)
    sub.add_argument("--records", metavar="JSON", help="Rows to enrich (needs --schema), inline or @file.")
    sub.add_argument("--include-domain", action="append", metavar="DOMAIN", help=HELP_INCLUDE)
    sub.add_argument("--exclude-domain", action="append", metavar="DOMAIN", help=HELP_EXCLUDE)


def _browse(commands: argparse._SubParsersAction[_Parser]) -> None:
    sub = _command(commands, "browse")
    sub.add_argument("task")
    sub.add_argument("--start-url", metavar="URL", help="The page the agent starts on.")
    sub.add_argument("--schema", metavar="JSON", help=HELP_SCHEMA)
    sub.add_argument("--max-steps", type=int, metavar="N", help="Most actions the agent may take.")
    sub.add_argument("--allowed-domain", action="append", metavar="DOMAIN", help="Keep the agent on it. Repeatable.")


def _live_data(commands: argparse._SubParsersAction[_Parser]) -> None:
    group = commands.add_parser("live-data", help=HELP_COMMAND["live-data"], description=HELP_COMMAND["live-data"])
    actions = group.add_subparsers(dest="action", required=True, metavar="ACTION", parser_class=_Parser)
    sources = actions.add_parser("sources", help="List or search a vendor's catalogue of sources.")
    sources.add_argument("-p", "--provider", help=HELP_PROVIDER)
    sources.add_argument("-q", "--query", help="Search the catalogue, e.g. 'amazon product'.")
    sources.add_argument("--limit", type=int, metavar="N", help="Sources per page.")
    sources.add_argument("--offset", type=int, metavar="N", help="Sources to skip.")
    source = actions.add_parser("source", help="Describe one source: its inputs and output schema.")
    source.add_argument("name")
    source.add_argument("-p", "--provider", help=HELP_PROVIDER)
    run = actions.add_parser("run", help="Run one source and return its records (bills per run).")
    run.add_argument("name")
    run.add_argument("-p", "--provider", help=HELP_PROVIDER)
    run.add_argument("--input", action="append", metavar="KEY=VALUE", help="One of the source's inputs. Repeatable.")
    run.add_argument("--inputs", metavar="JSON", help="All of the source's inputs as one JSON object, or @file.")
    run.add_argument("--format", choices=get_args(ExtractFormat), help="Also return the page in this format.")
    run.add_argument("--option", action="append", metavar="KEY=VALUE", help=HELP_OPTION)
    for action in (sources, source, run):
        _output_flags(action, default=argparse.SUPPRESS)
