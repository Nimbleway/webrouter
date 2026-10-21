"""classify() and execute() for :class:`~web_router.WebRouter`: the decider's labels to a Route, and a Route run.

Kept out of ``client.py`` so the facade stays a facade: each WebRouter method
here is one line that hands the router in.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Any

from web_router.constants import PROVIDER_AUTO
from web_router.constants_decisions import (
    MSG_ROUTE_NO_DECIDER,
    ROUTE_DEFAULT_LABELS,
    ROUTE_FALLBACK_DECIDER_ERROR,
    ROUTE_FALLBACK_LOW_CONFIDENCE,
    ROUTE_LABEL_TOOL,
    ROUTE_METHOD_BROWSE,
    ROUTE_METHOD_EXTRACT,
    ROUTE_METHOD_RESEARCH,
    ROUTE_METHOD_SEARCH,
    ROUTE_QUESTIONS,
)
from web_router.decisions.route import Answer, ChainEntry, Route, label_values, route_from_labels, urls_in
from web_router.exceptions import ConfigurationError
from web_router.registry import (
    create_browse_provider,
    create_extract_provider,
    create_provider,
    create_research_provider,
)

if TYPE_CHECKING:
    from web_router.client import JsonSchema, WebRouter
    from web_router.providers.base import BrowseProvider, ExtractProvider, ResearchProvider, SearchProvider
    from web_router.types import BrowseResponse, ExtractResponse, ResearchResponse, SearchResponse

    Response = SearchResponse | ExtractResponse | ResearchResponse | BrowseResponse


def classify(
    router: WebRouter,
    query: str,
    *,
    urls: str | list[str] | None = None,
    output_schema: JsonSchema | None = None,
) -> Route:
    """The labels step, then Route assembly: see :meth:`~web_router.WebRouter.classify`."""
    if router.decider is None:
        raise ConfigurationError(MSG_ROUTE_NO_DECIDER)
    given = [urls] if isinstance(urls, str) else list(urls or [])
    found = given or urls_in(query)
    chains = _default_chains(router)
    labels, values, fallback, latency = _labels(router.decider, query, given=bool(given), output_schema=output_schema)
    method, chain, reason = route_from_labels(query, values, chains, urls=found, output_schema=output_schema)
    # The raw tool label, before an unsure one took its default, so the advice can say where it leaned.
    tool = labels.get(ROUTE_LABEL_TOOL)
    requested = tool.choice if tool is not None else values[ROUTE_LABEL_TOOL]
    if output_schema is not None:
        requested = ROUTE_METHOD_RESEARCH
    return Route(
        query=query,
        method=method,
        chain=chain,
        urls=found,
        output_schema=output_schema,
        labels=labels,
        fallback=tuple(r for r in (fallback, reason) if r),
        requested=requested,
        latency_ms=latency,
    )


def _labels(
    decider: Any, query: str, *, given: bool, output_schema: JsonSchema | None
) -> tuple[dict[str, Answer], dict[str, str], str | None, float]:
    """The decider's labels, each label's value, the fallback reason if any, and the decider's latency in ms."""
    if given:
        return {}, {**ROUTE_DEFAULT_LABELS, ROUTE_LABEL_TOOL: ROUTE_METHOD_EXTRACT}, None, 0.0
    if output_schema is not None:
        return {}, dict(ROUTE_DEFAULT_LABELS), None, 0.0
    state = {"query": query, "asked_on": date.today().isoformat()}
    fallback: str | None = None
    started = time.perf_counter()
    # A wrapping decider's passthrough= types are a caller's policy: they stop the run, not route it.
    passthrough: tuple[type[BaseException], ...] = tuple(getattr(decider, "passthrough", ()) or ())
    try:
        labels = decider.decide(state, ROUTE_QUESTIONS)
        values, unsure = label_values(labels)  # inside, so a malformed result is a decider error too
    except Exception as exc:  # any decider failure, a third-party one's TimeoutError included, takes the default
        if passthrough and isinstance(exc, passthrough):
            raise
        labels, fallback = {}, ROUTE_FALLBACK_DECIDER_ERROR
        values, unsure = label_values(labels)
    latency_ms = (time.perf_counter() - started) * 1000
    if unsure and fallback is None:
        fallback = ROUTE_FALLBACK_LOW_CONFIDENCE
    return labels, values, fallback, latency_ms


def execute(router: WebRouter, route: Route, **overrides: Any) -> Response:
    """Run a :class:`Route` with the router's methods, on the route's chain. ``overrides`` go to the method."""
    # The route's own arguments are defaults: an override of the same name wins, rather than colliding.
    # A provider override is used as given: the route's own auto provider is built only when it will run.
    kwargs: dict[str, Any] = {} if "provider" in overrides else {"provider": _routed_provider(router, route)}
    start_url = route.urls[0] if route.urls else None
    runs: dict[str, tuple[Callable[..., Response], dict[str, Any]]] = {
        ROUTE_METHOD_SEARCH: (router.search, {"query": route.query}),
        ROUTE_METHOD_EXTRACT: (router.extract, {"urls": route.urls}),
        ROUTE_METHOD_RESEARCH: (router.research, {"query": route.query, "output_schema": route.output_schema}),
        ROUTE_METHOD_BROWSE: (router.browse, {"task": route.query, "start_url": start_url}),
    }
    run, arguments = runs.get(route.method, runs[ROUTE_METHOD_SEARCH])
    return run(**{**kwargs, **arguments, **overrides})


def _default_chains(router: WebRouter) -> dict[str, tuple[ChainEntry, ...]]:
    """Each method's auto chain as the router's own ``auto`` would resolve it, a configured ``chain`` included."""
    from web_router.providers.auto import (
        AutoBrowseProvider,
        AutoExtractProvider,
        AutoProvider,
        AutoResearchProvider,
        _members_and_chain,
    )

    config = router._config_for(PROVIDER_AUTO)
    defaults = {
        ROUTE_METHOD_SEARCH: AutoProvider.default_chain,
        ROUTE_METHOD_EXTRACT: AutoExtractProvider.default_chain,
        ROUTE_METHOD_RESEARCH: AutoResearchProvider.default_chain,
        ROUTE_METHOD_BROWSE: AutoBrowseProvider.default_chain,
    }
    return {
        method: _members_and_chain(config.get("chain"), config["members_config"], method, default)[1]
        for method, default in defaults.items()
    }


def _routed_provider(
    router: WebRouter, route: Route
) -> SearchProvider | ExtractProvider | ResearchProvider | BrowseProvider:
    """An auto provider on the route's chain, cached so close() owns it and config keys reach it."""
    # The members' options belong to the key too: the same names with other settings are another provider.
    key = f"{PROVIDER_AUTO}:{json.dumps([list(e) for e in route.chain], sort_keys=True, default=repr)}"
    config = {**router._config_for(PROVIDER_AUTO), "chain": {route.method: [list(e) for e in route.chain]}}
    table: dict[str, tuple[dict[str, Any], Any]] = {
        ROUTE_METHOD_EXTRACT: (router._extract_instances, create_extract_provider),
        ROUTE_METHOD_RESEARCH: (router._research_instances, create_research_provider),
        ROUTE_METHOD_BROWSE: (router._browse_instances, create_browse_provider),
    }
    cache, factory = table.get(route.method, (router._instances, create_provider))
    with router._providers_lock:
        if key not in cache:
            cache[key] = factory(PROVIDER_AUTO, **config)
        routed: SearchProvider | ExtractProvider | ResearchProvider | BrowseProvider = cache[key]
        return routed
