"""The ``webrouter`` argument parser: one subcommand per router method.

Choices come from the library's own ``Literal`` types, so a value the router
would refuse is refused here first, and ``--help`` lists what each flag takes.
Every help string is in :data:`~web_router.constants_cli.HELP`.
"""

from __future__ import annotations

import argparse
import math
from typing import Any, NoReturn, get_args

from web_router.constants_cli import (
    CLI_DESCRIPTION,
    CLI_EPILOG,
    CLI_PROG,
    HELP,
    HELP_COMMAND,
    MSG_BAD_NUMBER,
    MSG_NEGATIVE,
    MSG_NOT_POSITIVE,
)
from web_router.types import ExtractFormat
from web_router.types_options import Recency, ResearchEffort, SearchDepth

_Commands = argparse._SubParsersAction  # argparse exports no public name for what add_subparsers returns


class UsageError(Exception):
    """The parser refused the command line; carries argparse's message so the caller can print it as JSON."""


class _Parser(argparse.ArgumentParser):
    """Raises instead of printing and exiting, so a usage error reaches stderr in the same JSON as any other.

    Abbreviations are off: ``--pre`` would otherwise be read as ``--pretty``, and a flag added later could
    change what a script's short spelling means.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        raise UsageError(f"{self.prog}: {message}")


def _non_negative_int(text: str) -> int:
    """A count or a position: refused here, before anything is sent, rather than forwarded to a vendor."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(MSG_BAD_NUMBER.format(kind="int", value=text)) from None
    if value < 0:
        raise argparse.ArgumentTypeError(MSG_NEGATIVE.format(value=value))
    return value


def _positive_finite_float(text: str) -> float:
    """A wait in seconds: finite and above 0. research and browse would start a billed run on any other."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(MSG_BAD_NUMBER.format(kind="float", value=text)) from None
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError(MSG_NOT_POSITIVE.format(value=text))
    return value


def _output_flags(parser: argparse.ArgumentParser, *, default: object) -> None:
    """``--pretty`` and ``--raw``, accepted before the command and after it, where agents tend to put them.

    A subcommand's copy defaults to ``SUPPRESS``, so leaving it out there never undoes the flag given before.
    """
    parser.add_argument("--pretty", action="store_true", default=default, help=HELP["pretty"])
    parser.add_argument("--raw", action="store_true", default=default, help=HELP["raw"])


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
    _output_flags(_subcommand(commands, "providers"), default=argparse.SUPPRESS)
    return parser


def _subcommand(commands: _Commands[_Parser], name: str, *, help_key: str | None = None) -> _Parser:
    text = HELP_COMMAND[help_key or name]
    return commands.add_parser(name, help=text, description=text)


def _command(commands: _Commands[_Parser], name: str) -> _Parser:
    """A routed call: the backend, its own options, a timeout and a cost cap."""
    sub = _subcommand(commands, name)
    sub.add_argument("-p", "--provider", help=HELP["provider"])
    sub.add_argument("--option", action="append", metavar="KEY=VALUE", help=HELP["option"])
    sub.add_argument("--timeout", type=_positive_finite_float, metavar="SECONDS", help=HELP["timeout"])
    sub.add_argument("--max-cost", type=float, metavar="USD", help=HELP["max_cost"])
    _output_flags(sub, default=argparse.SUPPRESS)
    return sub


def _domains(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--include-domain", action="append", metavar="DOMAIN", help=HELP["include_domain"])
    sub.add_argument("--exclude-domain", action="append", metavar="DOMAIN", help=HELP["exclude_domain"])


def _filters(sub: argparse.ArgumentParser) -> None:
    """The result filters search and answer share."""
    _domains(sub)
    sub.add_argument("--country", help=HELP["country"])
    sub.add_argument("--locale", help=HELP["locale"])
    sub.add_argument("--recency", choices=get_args(Recency), help=HELP["recency"])


def _search(commands: _Commands[_Parser]) -> None:
    sub = _command(commands, "search")
    sub.add_argument("query")
    sub.add_argument("-n", "--max-results", type=int, metavar="N", help=HELP["max_results"])
    sub.add_argument("--depth", choices=get_args(SearchDepth), help=HELP["depth"])
    sub.add_argument("--effort", choices=get_args(ResearchEffort), help=HELP["search_effort"])
    sub.add_argument("--data-apis-only", action="store_true", help=HELP["data_apis_only"])
    sub.add_argument("--no-data-apis", dest="data_apis", action="store_false", help=HELP["no_data_apis"])
    sub.add_argument("--configured-only", action="store_true", help=HELP["configured_only"])
    _filters(sub)


def _extract(commands: _Commands[_Parser]) -> None:
    sub = _command(commands, "extract")
    sub.add_argument("urls", nargs="+", metavar="URL")
    sub.add_argument("--format", choices=get_args(ExtractFormat), help=HELP["format"])


def _answer(commands: _Commands[_Parser]) -> None:
    sub = _command(commands, "answer")
    sub.add_argument("query")
    _filters(sub)


def _research(commands: _Commands[_Parser]) -> None:
    sub = _command(commands, "research")
    sub.add_argument("query")
    sub.add_argument("--effort", choices=get_args(ResearchEffort), help=HELP["research_effort"])
    sub.add_argument("--schema", metavar="JSON", help=HELP["schema"])
    sub.add_argument("--records", metavar="JSON", help=HELP["records"])
    _domains(sub)


def _browse(commands: _Commands[_Parser]) -> None:
    sub = _command(commands, "browse")
    sub.add_argument("task")
    sub.add_argument("--start-url", metavar="URL", help=HELP["start_url"])
    sub.add_argument("--schema", metavar="JSON", help=HELP["schema"])
    sub.add_argument("--max-steps", type=int, metavar="N", help=HELP["max_steps"])
    sub.add_argument("--allowed-domain", action="append", metavar="DOMAIN", help=HELP["allowed_domain"])


def _live_data(commands: _Commands[_Parser]) -> None:
    """``live-data sources|source|run``. No ``--timeout`` or ``--max-cost``: the library's live data takes neither."""
    live_data = _subcommand(commands, "live-data")
    _output_flags(live_data, default=argparse.SUPPRESS)
    actions = live_data.add_subparsers(dest="action", required=True, metavar="ACTION", parser_class=_Parser)
    sources = _subcommand(actions, "sources", help_key="live-data sources")
    sources.add_argument("-q", "--query", help=HELP["catalogue_query"])
    sources.add_argument("--limit", type=_non_negative_int, metavar="N", help=HELP["limit"])
    sources.add_argument("--offset", type=_non_negative_int, metavar="N", help=HELP["offset"])
    source = _subcommand(actions, "source", help_key="live-data source")
    source.add_argument("name")
    run = _subcommand(actions, "run", help_key="live-data run")
    run.add_argument("name")
    run.add_argument("--input", action="append", metavar="KEY=VALUE", help=HELP["input"])
    run.add_argument("--inputs", metavar="JSON", help=HELP["inputs"])
    run.add_argument("--format", choices=get_args(ExtractFormat), help=HELP["live_format"])
    run.add_argument("--option", action="append", metavar="KEY=VALUE", help=HELP["option"])
    for action in (sources, source, run):
        action.add_argument("-p", "--provider", help=HELP["provider"])
        _output_flags(action, default=argparse.SUPPRESS)
