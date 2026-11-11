"""``FeaturesRouter``: the built-in keyless router, and the default, that picks a prompt's method from its features.

Experimental. It calls nothing and needs no key: :func:`read_features` reads
four features from the prompt (:mod:`.sensors`), and the method table
(:mod:`.mapping`) gives the method for them. Backends are left in their static
order. It is asked by :meth:`~web_router.WebRouter.plan` and
:meth:`~web_router.WebRouter.run` only (``route_tools=()``), so ``search``,
``extract`` and the other methods run as they would with no router; a method
the caller pinned is left as pinned.
"""

from __future__ import annotations

from typing import Any

from web_router.constants_features import FEATURES_REASON, FEATURES_ROUTER_NAME, FEATURES_TIMEOUT_S
from web_router.routing.contract import RouteDecision, RouteRequest, ToolName
from web_router.routing.features.mapping import Features
from web_router.routing.features.sensors import find_url, kw_research, plural, verb


def read_features(prompt: str) -> Features:
    """The four features the method table reads in ``prompt``; :attr:`Features.tool` is the method it picks."""
    return _read(prompt)[0]


def _read(prompt: str) -> tuple[Features, str | None]:
    """The features, and the URL behind ``has_url``: read once, for the method and the URL it runs on."""
    url = find_url(prompt)
    features = Features(
        verb=verb(prompt), has_url=url is not None, plural=plural(prompt), kw_research=kw_research(prompt)
    )
    return features, url


class FeaturesRouter:
    """What ``router="features"`` builds, and what a router built with no ``router=`` attaches."""

    router_name = FEATURES_ROUTER_NAME
    route_tools: tuple[ToolName, ...] = ()
    timeout_s = FEATURES_TIMEOUT_S

    def __repr__(self) -> str:
        return "FeaturesRouter()"

    def route(self, req: RouteRequest) -> RouteDecision:
        """The table's method for ``req.prompt``; extract and browse on the URL the sensors read, bare hosts included.

        Extract reads the URLs the plan read, or the sensors' one when the plan read none (a bare host). An
        extract left with no URL searches instead, by the merge rule. A pinned method is left as pinned: only the
        URL is given, so a pinned extract or browse of a bare host still has one.
        """
        features, url = _read(req.prompt)
        params: dict[ToolName, dict[str, Any]] | None = (
            {"extract": {"urls": list(req.urls) or [url]}, "browse": {"start_url": url}} if url else None
        )
        if req.pinned_tool is not None:
            return RouteDecision(params=params)
        return RouteDecision(
            tools=[features.tool],
            params=params,
            reason=FEATURES_REASON.format(
                verb=features.verb.value,
                has_url=features.has_url,
                plural=features.plural,
                kw_research=features.kw_research,
            ),
        )
