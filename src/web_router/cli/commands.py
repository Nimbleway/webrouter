"""One handler per subcommand: parsed arguments in, the router's response out.

Each calls the public method a library caller would, with the same arguments,
so the command line behaves exactly as the library does -- the same
validation, the same routing, the same errors.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from web_router.cli.args import CliInputError, json_object, json_records, pairs
from web_router.client import WebRouter
from web_router.registry_methods import configured_by_method
from web_router.routing.contract import Budget

Handler = Callable[[WebRouter, argparse.Namespace], Any]


def _budget(args: argparse.Namespace) -> Budget | None:
    """``--max-cost`` as the budget the method takes; the library refuses a non-finite or negative cap."""
    return None if args.max_cost is None else Budget(max_cost_usd=args.max_cost)


def _search(router: WebRouter, args: argparse.Namespace) -> Any:
    try:
        return _search_call(router, args)
    except ValueError as exc:
        # With --data-apis-only nothing is sent, so a plain ValueError (a web option set beside it, data APIs
        # turned off) is a refusal, not a failed call. Its subclasses keep their own status.
        if args.data_apis_only and type(exc) is ValueError:
            raise CliInputError(str(exc)) from exc
        raise


def _search_call(router: WebRouter, args: argparse.Namespace) -> Any:
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
        effort=args.effort,
        provider_options=pairs(args.option),
        timeout=args.timeout,
        budget=_budget(args),
        data_apis=args.data_apis,
        data_apis_only=args.data_apis_only,
        configured_only=args.configured_only,
    )


def _extract(router: WebRouter, args: argparse.Namespace) -> Any:
    return router.extract(
        args.urls,
        provider=args.provider,
        format=args.format,
        provider_options=pairs(args.option),
        timeout=args.timeout,
        budget=_budget(args),
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
        budget=_budget(args),
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
        budget=_budget(args),
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
        budget=_budget(args),
        **timeout,
    )


def _live_data(router: WebRouter, args: argparse.Namespace) -> Any:
    if args.action == "sources":
        return router.live_data_sources(provider=args.provider, query=args.query, limit=args.limit, offset=args.offset)
    if args.action == "source":
        return router.live_data_source(args.name, provider=args.provider)
    inputs = {**(json_object(args.inputs, "--inputs") or {}), **(pairs(args.input, typed=False) or {})}
    return router.live_data(
        args.name,
        inputs or None,
        provider=args.provider,
        format=args.format,
        provider_options=pairs(args.option),
    )


def _providers(_router: WebRouter, _args: argparse.Namespace) -> dict[str, list[str]]:
    """What the environment makes usable per method, the table the MCP server's capabilities show; calls nothing."""
    return configured_by_method()


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
