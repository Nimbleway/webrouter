"""One handler per subcommand: parsed arguments in, the router's response out.

Each calls the public method a library caller would, with the same arguments,
so the command line behaves exactly as the library does -- the same
validation, the same routing, the same errors.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from web_router.cli.args import json_object, json_records, pairs
from web_router.client import WebRouter
from web_router.registry import (
    configured_browse_providers,
    configured_browser_providers,
    configured_extract_providers,
    configured_live_data_providers,
    configured_providers,
    configured_research_providers,
)
from web_router.registry_answer import configured_answer_providers

Handler = Callable[[WebRouter, argparse.Namespace], Any]


def _search(router: WebRouter, args: argparse.Namespace) -> Any:
    return router.search(
        args.query,
        provider=args.provider,
        max_results=args.max_results,
        search_depth=args.depth,
        include_domains=args.include_domain,
        exclude_domains=args.exclude_domain,
        country=args.country,
        locale=args.locale,
        recency=args.recency,
        provider_options=pairs(args.option),
        timeout=args.timeout,
    )


def _extract(router: WebRouter, args: argparse.Namespace) -> Any:
    return router.extract(
        args.urls,
        provider=args.provider,
        format=args.format,
        provider_options=pairs(args.option),
        timeout=args.timeout,
    )


def _answer(router: WebRouter, args: argparse.Namespace) -> Any:
    return router.answer(
        args.query,
        provider=args.provider,
        include_domains=args.include_domain,
        exclude_domains=args.exclude_domain,
        country=args.country,
        locale=args.locale,
        recency=args.recency,
        provider_options=pairs(args.option),
        timeout=args.timeout,
    )


def _research(router: WebRouter, args: argparse.Namespace) -> Any:
    # Only a timeout the caller gave replaces the library's default wait; argparse cannot tell "unset" from None.
    timeout = {} if args.timeout is None else {"timeout": args.timeout}
    return router.research(
        args.query,
        provider=args.provider,
        output_schema=json_object(args.schema, "--schema"),
        records=json_records(args.records, "--records"),
        effort=args.effort,
        include_domains=args.include_domain,
        exclude_domains=args.exclude_domain,
        provider_options=pairs(args.option),
        **timeout,
    )


def _browse(router: WebRouter, args: argparse.Namespace) -> Any:
    timeout = {} if args.timeout is None else {"timeout": args.timeout}
    return router.browse(
        args.task,
        provider=args.provider,
        start_url=args.start_url,
        output_schema=json_object(args.schema, "--schema"),
        max_steps=args.max_steps,
        allowed_domains=args.allowed_domain,
        provider_options=pairs(args.option),
        **timeout,
    )


def _live_data(router: WebRouter, args: argparse.Namespace) -> Any:
    if args.action == "sources":
        return router.live_data_sources(provider=args.provider, query=args.query, limit=args.limit, offset=args.offset)
    if args.action == "source":
        return router.live_data_source(args.name, provider=args.provider)
    inputs = {**(json_object(args.inputs, "--inputs") or {}), **(pairs(args.input) or {})}
    return router.live_data(
        args.name,
        inputs or None,
        provider=args.provider,
        format=args.format,
        provider_options=pairs(args.option),
    )


def _providers(_router: WebRouter, _args: argparse.Namespace) -> dict[str, list[str]]:
    """What the environment makes usable per method; reads variables only and calls no backend."""
    return {
        "search": configured_providers(),
        "extract": configured_extract_providers(),
        "answer": configured_answer_providers(),
        "research": configured_research_providers(),
        "browse": configured_browse_providers(),
        "browser": configured_browser_providers(),
        "live_data": configured_live_data_providers(),
    }


HANDLERS: dict[str, Handler] = {
    "search": _search,
    "extract": _extract,
    "answer": _answer,
    "research": _research,
    "browse": _browse,
    "live-data": _live_data,
    "providers": _providers,
}
"""Each subcommand's handler, keyed by the name the parser gives it."""
